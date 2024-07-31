import os
import json
import requests
import re
import random
from datetime import datetime
from flask import Flask, request, Response, jsonify, stream_with_context
from requests.auth import HTTPBasicAuth
import logging
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# 加载环境变量
load_dotenv()

app = Flask(__name__)

# 尝试读取 config.json 文件
try:
    with open('config.json', 'r') as config_file:
        app_config = json.load(config_file)
except FileNotFoundError:
    app_config = {}

ACCOUNTS = {}
API_KEY = app_config.get('api_key') or os.environ.get('API_KEY')
REFRESH_TOKEN_URL = 'https://www.googleapis.com/oauth2/v4/token'
AUTH_DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# 配置日志
logging.basicConfig(level=logging.INFO)

"""
version为最新版本, 最新版本以对应模型的Vertex AI页面为准
locations为可用区域, 多地区配额可叠加。
支持区域以 https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude 为准
"""
DEFAULT_MODEL_CONFIG = {
    'claude-3-sonnet': {
        'version': 'claude-3-sonnet@20240229',  # 截至2024年6月26日最新版本，仅供展示，下同
        'locations': ["asia-southeast1", "us-central1", "us-east5"]
    },
    'claude-3-5-sonnet': {
        'version': 'claude-3-5-sonnet@20240620',
        'locations': ["us-east5", "europe-west1"]
    },
    'claude-3-opus': {
        'version': 'claude-3-opus@20240229',
        'locations': ["us-east5"]
    },
    'claude-3-haiku': {
        'version': 'claude-3-haiku@20240307',
        'locations': ["europe-west1", "europe-west4", "us-central1", "us-east5"]
    },
    'meta/llama3-405b-instruct-maas': {
        'version': 'meta/llama3-405b-instruct-maas',
        'locations': ["us-central1"]
    }
}

MODEL_CONFIG = app_config.get('models', DEFAULT_MODEL_CONFIG)

# 设置 ACCOUNTS
ACCOUNTS = {}
if 'accounts' in app_config:
    for index, account in enumerate(app_config['accounts']):
        project_id = account.get('project_id', '').lower()
        if project_id:
            account_name = f"{project_id}_{index}"
            ACCOUNTS[account_name] = {k.lower(): v for k, v in account.items()}
            ACCOUNTS[account_name]['failureCount'] = 0

if not ACCOUNTS:
    for key, value in os.environ.items():
        if key.startswith('ACCOUNT_'):
            account_name = key.replace('ACCOUNT_', '').lower()
            try:
                account_data = json.loads(value)
                ACCOUNTS[account_name] = {k.lower(): v for k, v in account_data.items()}
                ACCOUNTS[account_name]['failureCount'] = 0
            except json.JSONDecodeError:
                logging.error(f"Error parsing account info for {account_name}")

logging.info(f"Loaded accounts: {list(ACCOUNTS.keys())}")
logging.info(f"API Key configured: {'Yes' if API_KEY else 'No'}")

current_account_index = 0
current_location_index = 0
request_count = 0
# 全局缓存字典,用于存储每个账号的access token和过期时间
TOKEN_CACHE = {}

def get_access_token():
    global current_account_index
    account_keys = list(ACCOUNTS.keys())
    if not account_keys:
        raise Exception('No available accounts')

    current_account_key = account_keys[current_account_index]
    current_account = ACCOUNTS[current_account_key]
    
    # 检查缓存中是否有有效的token
    if current_account_key in TOKEN_CACHE:
        token_info = TOKEN_CACHE[current_account_key]
        if token_info['expiry_time'] > datetime.utcnow().timestamp():
            return token_info['access_token']

    try:
        if 'refresh_token' in current_account:
            response = requests.post(REFRESH_TOKEN_URL, 
                json={
                    'client_id': current_account['client_id'],
                    'client_secret': current_account['client_secret'],
                    'refresh_token': current_account['refresh_token'],
                    'grant_type': 'refresh_token'
                },
                timeout=10  # 设置 10 秒超时
            )
            response.raise_for_status()
            data = response.json()
            access_token = data['access_token']
            expiry_timestamp = datetime.utcnow().timestamp() + data['expires_in']
        else:
            credentials = service_account.Credentials.from_service_account_info(
                current_account, scopes=[AUTH_DEFAULT_SCOPE]
            )

            # 检查凭证是否过期
            if not credentials.valid:
                credentials.refresh(Request())

            access_token = credentials.token
            # 检查是否刷新成功
            if not access_token:
                raise Exception("Failed to refresh the service account token.")
            expiry_timestamp = credentials.expiry.timestamp()

        logging.info(f'get_access_token: {access_token}')
        # 更新缓存
        TOKEN_CACHE[current_account_key] = {
            'access_token': access_token,
            'expiry_time': expiry_timestamp - 120
        }

        current_account['failureCount'] = 0
        return access_token
    except requests.RequestException as e:
        logging.error(f'Error obtaining access token: {str(e)}')
        # Cleared all token caches
        TOKEN_CACHE.clear() 

        current_account['failureCount'] += 1
        logging.info(f"Account {current_account_key} failure count: {current_account['failureCount']}")

        if len(ACCOUNTS) == 1:
            # If there's only one account, raise the exception immediately
            raise Exception(f"Error obtaining access token for the only account: {str(e)}")

        if current_account['failureCount'] >= 3:
            logging.error(f"Account {current_account_key} has failed 3 times. Removing from rotation.")
            del ACCOUNTS[current_account_key]

        rotate_account()
        return get_access_token()  # Retry with a new account

def rotate_account():
    global current_account_index, current_location_index, request_count
    request_count += 1
    account_keys = list(ACCOUNTS.keys())
    if not account_keys:
        raise Exception('No available accounts')

    if request_count >= 3:
        request_count = 0
        current_account_index = (current_account_index + 1) % len(account_keys)
    current_account_key = account_keys[current_account_index]
    logging.info(f"Rotating to account: {current_account_key}, request count: {request_count}")

def get_location(model):
    config = MODEL_CONFIG[model]
    locations = config['locations']
    return random.choice(locations)

def construct_api_url(location, model):
    current_account_key = list(ACCOUNTS.keys())[current_account_index]
    current_account = ACCOUNTS[current_account_key]
    if model.startswith('meta'):
        return f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{current_account['project_id']}/locations/{location}/endpoints/openapi/chat/completions"
    else:
        return f"https://{location}-aiplatform.googleapis.com/v1/projects/{current_account['project_id']}/locations/{location}/publishers/anthropic/models/{model}:streamRawPredict"

def merge_messages(messages):
    # 如果 messages 为空,直接返回空列表
    if not messages:  
        return []

    merged_messages = []
    # 1.检查第一条消息的role,如果不是user或system,则插入一条新消息
    if messages and messages[0]['role'] not in ['user', 'system']:
        merged_messages.append({"role": "user", "content": "start"})

    last_role = None
    # 2.丢弃相同角色的连续消息
    for message in messages:
        if message['role'] == last_role:
            logging.info(f'drop message: {message}')
        else:
            last_role = message['role']
            merged_messages.append(message)

    # 3.检查最后一条消息的role,如果不是user,则插入一条新消息
    if merged_messages and merged_messages[-1]['role'] != 'user':
        merged_messages.append({"role": "user", "content": "start"})

    return merged_messages

@app.route('/v1/messages', methods=['POST'])
def handle_claude_request():
    api_key = request.headers.get('x-api-key')
    if api_key != API_KEY:
        return jsonify({
            "type": "error",
            "error": {
                "type": "permission_error",
                "message": "Invalid API key."
            }
        }), 403

    try:
        request_body = request.json
        model_with_version = request_body['model']
        base_model = re.sub(r'-\d{8}$', '', model_with_version)
        
        if base_model in list(MODEL_CONFIG.keys()):
            if re.search(r'-(\d{8})$', model_with_version):
                model = re.sub(r'-(\d{8})$', r'@\1', model_with_version)
            else:
                model = base_model
        else:
            return jsonify({
                "type": "error",
                "error": {
                    "type": "invalid_model",
                    "message": "The specified model is not in the allowed list."
                }
            }), 400

        access_token = get_access_token()
        location = get_location(base_model)
        api_url = construct_api_url(location, model)

        request_body['anthropic_version'] = "vertex-2023-10-16"
        request_body.pop('model', None)

        request_body['messages'] = merge_messages(request_body['messages'])

        current_account_key = list(ACCOUNTS.keys())[current_account_index]
        logging.info(f"Using account: {current_account_key}, location: {location}, request count: {request_count}")
        # logging.info(f'Request body: {json.dumps(request_body, indent=2)}')
        logging.info(f'API URL: {api_url}')

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json; charset=utf-8'
        }

        response = requests.post(api_url, json=request_body, headers=headers, stream=True)
        response.raise_for_status()

        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                yield chunk

        rotate_account()

        return Response(stream_with_context(generate()), content_type=response.headers['Content-Type'])

    except Exception as e:
        logging.error(f'Error in request: {str(e)}')
        if str(e) == 'No available accounts':
            return jsonify({
                "type": "error",
                "error": {
                    "type": "service_unavailable",
                    "message": "No available accounts. Please try again later."
                }
            }), 503
        else:
            # Cleared all token caches
            TOKEN_CACHE.clear() 
            return jsonify({
                "type": "error",
                "error": {
                    "type": "internal_error",
                    "message": "An internal error occurred. Please try again later."
                }
            }), 500

@app.route('/api/chat', methods=['POST'])
@app.route('/v1/chat/completions', methods=['POST'])
def handle_llama_request():
    # logging.info(f'Request headers: {request.headers}')
    # 或者认证头的信息并删除前面的Bearer 
    api_key = request.headers.get('Authorization')[7:]
    if api_key != API_KEY:
        return jsonify({
            "type": "error",
            "error": {
                "type": "permission_error",
                "message": "Invalid API key."
            }
        }), 403
    
    try:
        request_body = request.json
        base_model = request_body['model']        
        if base_model in list(MODEL_CONFIG.keys()):
            model = base_model
        else:
            return jsonify({
                "type": "error",
                "error": {
                    "type": "invalid_model",
                    "message": "The specified model is not in the allowed list."
                }
            }), 400

        access_token = get_access_token()
        location = get_location(base_model)
        api_url = construct_api_url(location, model)

        current_account_key = list(ACCOUNTS.keys())[current_account_index]
        logging.info(f"Using account: {current_account_key}, location: {location}, request count: {request_count}")
        # logging.info(f'Request body: {json.dumps(request_body, indent=2)}')
        logging.info(f'API URL: {api_url}')

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json; charset=utf-8'
        }

        response = requests.post(api_url, json=request_body, headers=headers, stream=True)
        response.raise_for_status()

        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                yield chunk

        rotate_account()

        # 检查request_body中是否存在stream且值为true
        is_stream = request_body.get('stream', False) == True
        # 根据is_stream设置content_type
        content_type = 'text/event-stream; charset=UTF-8' if is_stream else 'application/json; charset=utf-8'
        return Response(
            stream_with_context(generate()), 
            content_type=content_type, 
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': '*'
            })

    except Exception as e:
        logging.error(f'Error in request: {str(e)}')
        if str(e) == 'No available accounts':
            return jsonify({
                "type": "error",
                "error": {
                    "type": "service_unavailable",
                    "message": "No available accounts. Please try again later."
                }
            }), 503
        else:
            # Cleared all token caches
            TOKEN_CACHE.clear() 
            return jsonify({
                "type": "error",
                "error": {
                    "type": "internal_error",
                    "message": "An internal error occurred. Please try again later."
                }
            }), 500

@app.route('/', methods=['GET'])
@app.route('/<path:path>', methods=['GET'])
def handle_not_found(path=''):
    return jsonify({
        "type": "error",
        "error": {
            "type": "not_found",
            "message": "The requested resource was not found."
        }
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)))
