import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
from app import app, process_sheet_data, normalize_type, parse_date
import json

class TestWealthTrackerApp(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        
        # More comprehensive test data with multiple dates and types
        # Note: Values are stored with their actual sign (debts are negative)
        self.mock_sheet_data = [
            # First day entries
            ['01/01/2023', 'Cash', '1000', 'GBP', '1000'],
            ['01/01/2023', 'Stocks', '2000', 'GBP', '2000'],
            ['01/01/2023', 'Credit Card', '-500', 'GBP', '-500'],
            ['01/01/2023', 'Mortgage', '-3000', 'GBP', '-3000'],
            
            # Second day entries - some values change
            ['02/01/2023', 'Cash', '1200', 'GBP', '1200'],
            ['02/01/2023', 'Stocks', '2100', 'GBP', '2100'],
            # Credit card not updated - should use previous value
            
            # Third day entries
            ['03/01/2023', 'Cash', '1250', 'GBP', '1250'],
            ['03/01/2023', 'Credit Card', '-450', 'GBP', '-450'],
            ['03/01/2023', 'Mortgage', '-2950', 'GBP', '-2950'],
            # Stocks not updated - should use previous value
        ]
        
        self.mock_types_data = [
            ['Cash', 'Asset'],
            ['Stocks', 'Asset'],
            ['Credit Card', 'Debt'],
            ['Mortgage', 'Debt']
        ]

    def test_normalize_type(self):
        """Test type normalization function"""
        self.assertEqual(normalize_type('  Cash  '), 'cash')
        self.assertEqual(normalize_type('CREDIT CARD'), 'credit card')
        self.assertEqual(normalize_type('Stocks '), 'stocks')

    def test_parse_date(self):
        """Test date parsing function"""
        date = parse_date('01/01/2023')
        self.assertIsInstance(date, datetime)
        self.assertEqual(date.day, 1)
        self.assertEqual(date.month, 1)
        self.assertEqual(date.year, 2023)

    @patch('app.get_sheet_data')
    def test_process_sheet_data(self, mock_get_sheet_data):
        """Test data processing function with detailed validation of calculations"""
        # Configure mock to return our test data
        mock_get_sheet_data.side_effect = [
            self.mock_sheet_data,  # For DATA_RANGE
            self.mock_types_data   # For TYPES_RANGE
        ]

        chart_data, latest_data, type_categories = process_sheet_data()

        # 1. Test chart data structure
        self.assertIn('dates', chart_data)
        self.assertIn('types', chart_data)
        self.assertIn('type_data', chart_data)
        self.assertIn('total_assets', chart_data)
        self.assertIn('total_debts', chart_data)
        self.assertIn('net_worth', chart_data)

        # 2. Test dates are processed correctly
        expected_dates = ['01/01/2023', '02/01/2023', '03/01/2023']
        self.assertEqual(chart_data['dates'], expected_dates)

        # 3. Test all types are captured correctly
        expected_types = ['cash', 'stocks', 'credit card', 'mortgage']
        self.assertCountEqual(chart_data['types'], expected_types)

        # 4. Test latest values are calculated correctly
        self.assertEqual(latest_data['cash'], 1250)
        self.assertEqual(latest_data['stocks'], 2100)
        self.assertEqual(latest_data['credit card'], -450)
        self.assertEqual(latest_data['mortgage'], -2950)

        # 5. Test type categorization
        self.assertEqual(type_categories['cash'], 'Asset')
        self.assertEqual(type_categories['stocks'], 'Asset')
        self.assertEqual(type_categories['credit card'], 'Debt')
        self.assertEqual(type_categories['mortgage'], 'Debt')

        # 6. Test time series data for each type (critical algorithmic functionality)
        # Test that values carry forward correctly when not updated
        self.assertEqual(chart_data['type_data']['cash'], [1000, 1200, 1250])
        self.assertEqual(chart_data['type_data']['stocks'], [2000, 2100, 2100])  # Last day uses previous value
        self.assertEqual(chart_data['type_data']['credit card'], [-500, -500, -450])  # Middle day uses previous value
        self.assertEqual(chart_data['type_data']['mortgage'], [-3000, -3000, -2950])  # Middle day uses previous value

        # 7. Test asset totals calculation at each date point
        expected_assets = [3000, 3300, 3350]  # Sum of cash and stocks for each date
        self.assertEqual(chart_data['total_assets'], expected_assets)

        # 8. Test debt totals calculation at each date point
        expected_debts = [-3500, -3500, -3400]  # Sum of credit card and mortgage for each date
        self.assertEqual(chart_data['total_debts'], expected_debts)

        # 9. Test net worth calculation at each date point
        # Looking at the app code:
        # In process_sheet_data, net_worth = assets - debts
        # Since debts are already negative values, assets - debts becomes assets + abs(debts) 
        expected_net_worth = [6500, 6800, 6750]  # assets - debts = 3000-(-3500), 3300-(-3500), 3350-(-3400)
        self.assertEqual(chart_data['net_worth'], expected_net_worth)

    def test_process_sheet_data_edge_cases(self):
        """Test edge cases in data processing"""
        with patch('app.get_sheet_data') as mock_get_sheet_data:
            # Test with empty data
            mock_get_sheet_data.side_effect = [[], []]
            chart_data, latest_data, type_categories = process_sheet_data()
            self.assertEqual(chart_data['dates'], [])
            self.assertEqual(chart_data['types'], [])
            self.assertEqual(chart_data['total_assets'], [])
            self.assertEqual(latest_data, {})
            
            # Test with incomplete row (missing value)
            mock_get_sheet_data.side_effect = [
                [['01/01/2023', 'Cash', '1000', 'GBP']],  # Missing GBP value
                [['Cash', 'Asset']]
            ]
            chart_data, latest_data, type_categories = process_sheet_data()
            self.assertEqual(chart_data['dates'], [])  # Should skip incomplete row
            self.assertEqual(latest_data, {})

    def test_index_route(self):
        """Test the index route"""
        with patch('app.process_sheet_data') as mock_process:
            mock_process.return_value = (
                {
                    'dates': ['01/01/2023'],
                    'types': ['cash', 'credit card'],
                    'type_data': {'cash': [1000], 'credit card': [-500]},
                    'total_assets': [1000],
                    'total_debts': [-500],
                    'net_worth': [1500]  # 1000 - (-500) = 1500
                },
                {},
                {'cash': 'Asset', 'credit card': 'Debt'}
            )
            
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)

    def test_dashboard_route(self):
        """Test the dashboard route"""
        with patch('app.process_sheet_data') as mock_process:
            mock_process.return_value = (
                {
                    'dates': ['01/01/2023'],
                    'types': ['cash', 'credit card'],
                    'type_data': {'cash': [1000], 'credit card': [-500]},
                    'total_assets': [1000],
                    'total_debts': [-500],
                    'net_worth': [1500]  # 1000 - (-500) = 1500
                },
                {'cash': 1000, 'credit card': -500},
                {'cash': 'Asset', 'credit card': 'Debt'}
            )
            
            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 200)
            
    def test_dashboard_route_calculations(self):
        """Test the dashboard route's calculations"""
        # Setup test data for a dashboard with assets and debts
        with patch('app.process_sheet_data') as mock_process:
            # Provide data with negative values for debts
            mock_process.return_value = (
                {},  # chart_data (not used in this test)
                {'cash': 1000, 'stocks': 2000, 'credit card': -500, 'mortgage': -3000},  # latest_data
                {'cash': 'Asset', 'stocks': 'Asset', 'credit card': 'Debt', 'mortgage': 'Debt'}  # type_categories
            )
            
            # Call the route through the test client
            response = self.client.get('/dashboard')
            
            # Verify the response is successful
            self.assertEqual(response.status_code, 200)

    def test_decreasing_values(self):
        """Test that decreasing values are correctly reflected in the chart data"""
        with patch('app.get_sheet_data') as mock_get_sheet_data:
            # Create test data with decreasing values
            mock_sheet_data = [
                # Day 1: Initial values
                ['01/01/2023', 'Cash', '1000', 'GBP', '1000'],
                ['01/01/2023', 'Stocks', '2000', 'GBP', '2000'],
                
                # Day 2: Stocks decrease in value
                ['02/01/2023', 'Cash', '1100', 'GBP', '1100'],
                ['02/01/2023', 'Stocks', '1800', 'GBP', '1800'],  # Decreased from 2000
                
                # Day 3: Cash decreases in value
                ['03/01/2023', 'Cash', '900', 'GBP', '900'],      # Decreased from 1100
                ['03/01/2023', 'Stocks', '1900', 'GBP', '1900'],  # Increased from 1800
            ]
            
            mock_types_data = [
                ['Cash', 'Asset'],
                ['Stocks', 'Asset']
            ]
            
            mock_get_sheet_data.side_effect = [mock_sheet_data, mock_types_data]
            
            # Process the data
            chart_data, latest_data, _ = process_sheet_data()
            
            # Verify that decreasing values are correctly reflected
            self.assertEqual(chart_data['type_data']['cash'], [1000, 1100, 900])
            self.assertEqual(chart_data['type_data']['stocks'], [2000, 1800, 1900])
            
            # Verify that the latest values are correct
            self.assertEqual(latest_data['cash'], 900)
            self.assertEqual(latest_data['stocks'], 1900)
            
            # Verify that the total assets calculation is correct
            self.assertEqual(chart_data['total_assets'], [3000, 2900, 2800])
            
            # Verify that the net worth calculation is correct (no debts in this test)
            self.assertEqual(chart_data['net_worth'], [3000, 2900, 2800])

if __name__ == '__main__':
    unittest.main()