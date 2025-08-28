from flask import Flask, render_template, jsonify, request
import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from collections import defaultdict
from datetime import datetime
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix
import httplib2
import certifi
from google_auth_httplib2 import AuthorizedHttp

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Optional local base path mounting (set BASE_PATH=/wealth to test locally under /wealth)
BASE_PATH = os.environ.get('BASE_PATH', '').rstrip('/')
if BASE_PATH:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    app.config['APPLICATION_ROOT'] = BASE_PATH
    app.wsgi_app = DispatcherMiddleware(Flask('root_app'), {BASE_PATH: app.wsgi_app})
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
app.static_folder = 'static'

# Google Sheets API setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SHEET_ID = '15wCXfKPNHCFp5L0a9prSTA0B1s4uoWKPTrQ9ZTWYG7U'
DATA_RANGE = 'Net Worth Data!A2:E'  # A:Date, B:Type, C:Value, D:Currency, E:Value (GBP)
TYPES_RANGE = 'Types!A2:B'

credentials = Credentials.from_service_account_file('wealthmanager-credentials.json', scopes=SCOPES)

# Configure HTTP client with explicit CA bundle to avoid local TLS issues
_ca_bundle_path = os.environ.get('SSL_CERT_FILE') or os.environ.get('REQUESTS_CA_BUNDLE') or certifi.where()
_base_http = httplib2.Http(ca_certs=_ca_bundle_path, timeout=30)
_authed_http = AuthorizedHttp(credentials, http=_base_http)
service = build('sheets', 'v4', http=_authed_http, cache_discovery=False)

@cache.memoize(timeout=3600)  # Cache for 1 hour
def get_sheet_data(range_name):
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SHEET_ID, range=range_name).execute()
        return result.get('values', [])
    except Exception as error:
        print(f"An error occurred while fetching sheet data: {error}")
        return []

def parse_date(date_str):
    return datetime.strptime(date_str, '%d/%m/%Y')

def normalize_type(type_str):
    return type_str.strip().lower()

def process_sheet_data():
    net_worth_data = get_sheet_data(DATA_RANGE)
    types_data = get_sheet_data(TYPES_RANGE)
    
    # Create a dictionary to map types to categories (with normalized keys)
    type_categories = {normalize_type(row[0]): row[1] for row in types_data}
    
    dates = set()
    types = set()
    type_latest_values = defaultdict(lambda: defaultdict(float))

    for row in net_worth_data:
        if len(row) < 5:
            continue
        date, type_, _, _, value_gbp = row
        date = parse_date(date)
        normalized_type = normalize_type(type_)
        dates.add(date)
        types.add(normalized_type)
        type_latest_values[normalized_type][date] = float(value_gbp)

    sorted_dates = sorted(dates)
    latest_date = sorted_dates[-1] if sorted_dates else None

    # Calculate the latest values for each type
    latest_data = {}
    for type_ in types:
        # Find the most recent date for this type that's not after the latest date
        type_dates = [d for d in type_latest_values[type_].keys() if d <= latest_date]
        if type_dates:
            most_recent_date = max(type_dates)
            latest_data[type_] = type_latest_values[type_][most_recent_date]
        else:
            latest_data[type_] = 0

    # Calculate time series data
    chart_data = {
        'dates': [d.strftime('%d/%m/%Y') for d in sorted_dates],
        'types': list(types),
        'type_data': defaultdict(list),
        'total_assets': [],
        'total_debts': [],
        'net_worth': []
    }

    for date in sorted_dates:
        assets = 0
        debts = 0
        for type_ in types:
            # Find the most recent date for this type that's not after the current date
            type_dates = [d for d in type_latest_values[type_].keys() if d <= date]
            if type_dates:
                most_recent_date = max(type_dates)
                latest_value = type_latest_values[type_][most_recent_date]
            else:
                latest_value = 0
                
            chart_data['type_data'][type_].append(latest_value)
            if type_categories.get(type_) == 'Asset':
                assets += latest_value
            elif type_categories.get(type_) == 'Debt':
                debts += latest_value
        chart_data['total_assets'].append(assets)
        chart_data['total_debts'].append(debts)
        chart_data['net_worth'].append(assets - debts)

    return chart_data, latest_data, type_categories

@app.route('/refresh-cache', methods=['POST'])
def refresh_cache():
    # Clear memoized cache for get_sheet_data and any other cached functions
    try:
        cache.clear()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route('/')
def index():
    chart_data, _, type_categories = process_sheet_data()
    return render_template('index.html', **chart_data, type_categories=type_categories)

@app.route('/dashboard')
def dashboard():
    chart_data, latest_data, type_categories = process_sheet_data()
    
    # Separate assets and debts
    assets = {t: v for t, v in latest_data.items() if type_categories.get(t) == 'Asset'}
    debts = {t: v for t, v in latest_data.items() if type_categories.get(t) == 'Debt'}
    
    total_assets = sum(assets.values())
    total_debts = sum(debts.values())
    net_worth = total_assets - total_debts
    
    # Build color palettes: greens for assets, reds for debts
    green_palette = ['#e6f4ea', '#c9eecd', '#a8e6b5', '#85db9e', '#5cc786', '#35b26f', '#1e9e5a', '#178a4b']
    red_palette = ['#fde7e9', '#fccdd2', '#f9aab3', '#f48796', '#ec6279', '#dd3d5e', '#c41f46', '#a50e34']

    asset_colors = [green_palette[i % len(green_palette)] for i in range(len(assets))]
    debt_colors = [red_palette[i % len(red_palette)] for i in range(len(debts))]

    pie_data = {
        'labels': list(assets.keys()) + list(debts.keys()),
        'datasets': [{
            'data': list(assets.values()) + [-v for v in debts.values()],
            'backgroundColor': asset_colors + debt_colors
        }]
    }
    
    summary_data = {
        'total_assets': total_assets,
        'total_debts': total_debts,
        'net_worth': net_worth,
        'pie_data': pie_data,
        'latest_data': latest_data,
        'type_categories': type_categories
    }
    
    return render_template('dashboard.html', summary_data=summary_data)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
