import os
import json
import random
import base64
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, disconnect
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, create_refresh_token, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from base64 import b64decode
from jwt import ExpiredSignatureError
from datetime import timedelta, datetime, timezone
from utils import generate_verification_code, prepare_data_for_client, prepare_data_for_db
from mail import send_email
from database import Database
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder=os.getenv('FLASK_STATIC_FOLDER', None))
app.config["MONGO_URI"] = os.getenv('MONGODB_URI', None)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 16 megabytes
app.config["JWT_SECRET_KEY"] = base64.b64encode(os.urandom(32)).decode('utf-8')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=60)
CORS(app, resources={r"/*": {"origins": "*"}})
database = Database(app)
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=1024 * 1024 * 1024)
jwt = JWTManager(app)

@app.route('/login', methods=['POST'])
def login():
    email = request.json.get('email', None)
    password = request.json.get('password', None)
    user = database.user.get({"email": email})
    if user:
        if check_password_hash(user['password'], password):
            if user["email_verified"] == True:
                access_token = create_access_token(identity=email)
                refresh_token = create_refresh_token(identity=email)
                return jsonify(access_token=access_token, refresh_token=refresh_token, follow={"link": "/crypto", "replace": True}), 200
            else:
                verify_code = generate_verification_code()
                database.code.create(user["_id"], verify_code, datetime.now(timezone.utc))
                send_email(email, f'Your verify code - {verify_code}', 'Paste this code to HOH.')
                return jsonify(code_id=code_id, verify_type="email", follow={"link": "/verify", "replace": False}), 200
        else:
            return jsonify(error="Неверный E-mail или пароль"), 200
    else:
        return jsonify(error="Неверный E-mail или пароль"), 200

@app.route('/signup', methods=['POST'])
def signup():
    email = request.json.get('email', None)
    username = request.json.get('username', None)
    password = request.json.get('password', None)
    inviteCode = request.json.get('inviteCode', None)
    if database.user.is_exists({"username": username}):
        return jsonify(error="Пользователь с таким именем уже существует"), 200
    if database.user.is_exists({"email": email}):
        return jsonify(error="Пользователь с таким E-mail уже существует"), 200
    hashed_password = generate_password_hash(password)
    verify_code = generate_verification_code()
    user_id = database.user.create(
        referral_id=None, 
        avatar=None, 
        username=username,
        email=email, 
        email_verified=False, 
        wallet=None, 
        wallet_verified=False, 
        game_balance=2000, 
        balance=0,
        password=hashed_password
    )
    code_id = database.code.create(user_id, verify_code, datetime.now(timezone.utc))
    send_email(email, f'Your verify code - {verify_code}', 'Paste this code to HOH.')
    return jsonify(code_id=code_id, verify_type="email", follow={"link": "/verify", "replace": False}), 200

@app.route('/verify', methods=['POST'])
def verify():
    code_id = request.json.get('code_id', None)
    verify_type = request.json.get('verify_type', None)
    code = request.json.get('code', None)
    result = database.code.get({"_id": code_id})
    if not result:
        return jsonify(error="\'code_id\' not found"), 200
    elif int(result['verify_code']) != int(code):
        return jsonify(error="\'verify_code\' is incorrect"), 200
    else:
        user = database.user.get({"_id": result["user_id"]})
        if not user:
            return jsonify(error="\'user\' not found"), 200
        else:
            access_token = create_access_token(identity=user["email"])
            refresh_token = create_refresh_token(identity=user["email"])
            database.code.remove({"_id": result["_id"]})
            if verify_type == "email":
                settings = database.settings.get()
                database.energy.create(result["user_id"], settings["default_energy_limit"], settings["default_energy_value"], settings["default_energy_minutes"])
                database.user.update(result["user_id"], {"$set": {"email_verified": True}})
                return jsonify(access_token=access_token, refresh_token=refresh_token, follow={"link": "/crypto", "replace": True}), 200
            elif verify_type == "create_wallet" or verify_type == "import_wallet":
                database.user.update(result["user_id"], {"$set": {"wallet_verified": True}})
                return jsonify(wallet=user["wallet"], follow={"link": "/game", "replace": True}), 200

@app.route('/resend-code', methods=['POST'])
def resend_code():
    result = database.code.get({"_id": request.json.get('code_id', None)})
    if not result:
        return jsonify(error="\'code_id\' not found"), 200
    else:
        user = database.user.get({"_id": result["user_id"]})
        if not user:
            return jsonify(error="\'user\' not found"), 200
        else:
            verify_code = generate_verification_code()
            database.code.update(result["_id"], {"$set": {"verify_code": verify_code}})
            send_email(user["email"], f'Your verify code - {verify_code}', 'Paste this code to HOH.')
            return jsonify(code_id=result["_id"]), 200

@app.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    current_user = get_jwt_identity()
    access_token = create_access_token(identity=current_user)
    refresh_token = create_refresh_token(identity=current_user)
    return jsonify(access_token=access_token, refresh_token=refresh_token), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

@socketio.on('connect')
@jwt_required()
def handle_connect():
    print('A user connected')

@socketio.on('disconnect')
@jwt_required()
def handle_disconnect():
    print('A user disconnected')

@socketio.on('message')
@jwt_required()
def handle_message(message):
    message = json.loads(message)
    print(message)
    current_user = get_jwt_identity()
    user = database.user.get({"email": current_user})
    if message[0] == 'user':
        if message[1] == 'get':
            emit('message', json.dumps([message[0], message[1], user]))
        elif message[1] == 'add_wallet':
            if "wallet_verified" not in user:
                return
            if user["wallet_verified"]:
                emit('message', json.dumps([message[0], message[1], {"follow": {"link": "/game", "replace": True}}]))
                return
            verify_code = generate_verification_code()
            database.user.update(user["_id"], {"$set": {"wallet": message[2], "wallet_verified": False}})
            code_id = database.code.create(user["_id"], verify_code, datetime.now(timezone.utc))
            send_email(user["email"], f'Your verify code - {verify_code}', 'Paste this code to HOH.')
            emit('message', json.dumps([message[0], message[1], {"code_id": code_id, "verify_type": message[3], "follow": {"link": "/verify", "replace": True}}]))
        elif message[1] == 'leaders':
            leaders = database.user.search({}, 'game_balance')
            emit('message', json.dumps([message[0], message[1], leaders]))
    elif message[0] == 'energy':
        if message[1] == 'get':
            energy = database.energy.get({"user_id": user["_id"]})
            if energy:
                generation = database.generation.get({"energy_id": energy["_id"], "ended": False})
                if not generation:
                    emit('message', json.dumps([message[0], message[1], energy]))
                    return
                if datetime.now(timezone.utc) >= datetime.strptime(generation["end_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc):
                    if energy["value"] < energy["limit"]:
                        energy["value"] += 1
                        database.energy.update(energy["_id"], {"$set": {"value": energy["value"]}})
                        database.generation.update(generation["_id"], {"$set": {"ended": True}})
                        emit('message', json.dumps([message[0], message[1], energy]))
                else:
                    emit('message', json.dumps([message[0], message[1], energy]))
            else:
                settings = database.settings.get()
                energy_id = database.energy.create(user["_id"], settings["default_energy_limit"], settings["default_energy_value"], settings["default_energy_minutes"])
                emit('message', json.dumps([message[0], message[1], database.energy.get({"_id": energy_id})]))
    elif message[0] == 'generation':
        if message[1] == 'get':
            energy = database.energy.get({"_id": message[2]})
            if not energy:
                return
            generation = database.generation.get({"energy_id": energy["_id"], "ended": False})
            if generation:
                if datetime.now(timezone.utc) >= datetime.strptime(generation["end_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc):
                    if energy["value"] < energy["limit"]:
                        database.energy.update(energy["_id"], {"$set": {"value": energy["value"] + 1}})
                        database.generation.update(generation["_id"], {"$set": {"ended": True}})
                else:
                    emit('message', json.dumps([message[0], message[1], message[2], generation]))
        elif message[1] == 'add':
            energy = database.energy.get({"_id": message[2]})
            if not energy:
                return
            generation = database.generation.get({"energy_id": energy["_id"], "ended": False})
            if generation:
                if datetime.now(timezone.utc) >= datetime.strptime(generation["end_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc):
                    if energy["value"] < energy["limit"]:
                        database.energy.update(energy["_id"], {"$set": {"value": energy["value"] + 1}})
                        database.generation.update(generation["_id"], {"$set": {"ended": True}})
                        now = datetime.now(timezone.utc)
                        generation_id = database.generation.create(energy["_id"], now + timedelta(minutes=energy["minutes"]), now)
                        emit('message', json.dumps([message[0], message[1], message[2], database.generation.get({"_id": generation_id})]))
                else:
                    emit('message', json.dumps([message[0], message[1], message[2], generation]))
            else:
                if energy["value"] < energy["limit"]:
                    now = datetime.now(timezone.utc)
                    generation_id = database.generation.create(energy["_id"], now + timedelta(minutes=energy["minutes"]), now)
                    emit('message', json.dumps([message[0], message[1], message[2], database.generation.get({"_id": generation_id})]))
    elif message[0] == 'boost':
        if message[1] == 'use':
            energy = database.energy.get({"_id": message[2]})
            if not energy:
                return
            boost = database.boost.get({"_id": message[3]})
            if not boost:
                return
            settings = database.settings.get()
            game_balance = user["game_balance"]
            if boost["name"] == "minutes:-1":
                if energy["minutes"] > settings["min_energy_minutes"]:
                    if user["game_balance"] >= boost["price"] + boost["price"] * (settings["max_energy_minutes"] - energy["minutes"]):
                        database.user.update(user["_id"], {"$set": {"game_balance": user["game_balance"] - (boost["price"] + boost["price"] * (settings["max_energy_minutes"] - energy["minutes"]))}})
                        database.energy.update(energy["_id"], {"$set": {"minutes": energy["minutes"] - 1}})
                        emit('message', json.dumps([message[0], message[1], message[2], game_balance - (boost["price"] + boost["price"] * (settings["max_energy_minutes"] - energy["minutes"]))]))
                        database.user_boost.create(boost["_id"], user["game_balance"], energy["limit"], energy["minutes"], datetime.now(timezone.utc))
                        energy = database.energy.get({"_id": energy["_id"]})
                        emit('message', json.dumps(["energy", "get", energy]))
            elif boost["name"] == "limit:+1":
                if energy["limit"] < settings["max_energy_limit"]:
                    if user["game_balance"] >= boost["price"] + boost["price"] * (energy["limit"] - settings["min_energy_limit"]):
                        database.user.update(user["_id"], {"$set": {"game_balance": user["game_balance"] - (boost["price"] + boost["price"] * (energy["limit"] - settings["min_energy_limit"]))}})
                        database.energy.update(energy["_id"], {"$set": {"limit": energy["limit"] + 1}})
                        emit('message', json.dumps([message[0], message[1], message[2], game_balance - (boost["price"] + boost["price"] * (energy["limit"] - settings["min_energy_limit"]))]))
                        database.user_boost.create(boost["_id"], user["game_balance"], energy["limit"], energy["minutes"], datetime.now(timezone.utc))
                        energy = database.energy.get({"_id": energy["_id"]})
                        emit('message', json.dumps(["energy", "get", energy]))
        elif message[1] == 'get':
            boosts = database.boost.search({})
            emit('message', json.dumps(["boost", "get", boosts]))
    elif message[0] == 'game':
        energy = database.energy.get({"_id": message[1]})
        if not energy:
            return
        if energy["value"] <= 0:
            return
        cards = database.card.search({})
        if len(cards) < 3:
            return
        numbers = [card for card in cards]
        weights = [card['chance'] for card in cards]
        list_length = 3
        random_list = random.choices(numbers, weights, k=list_length)
        game_id = database.game.create(user["_id"], datetime.now(timezone.utc))
        database.deck.create(game_id, random_list, message[2])
        database.energy.update(energy["_id"], {"$set": {"value": energy["value"] - 1}})
        game_balance = user["game_balance"]
        balance = user["balance"]
        if random_list[message[2]]["name"].split(":")[0] == "game_balance_up":
            game_balance += int(random_list[message[2]]["name"].split(":")[1])
            database.user.update(user["_id"], {"$set": {"game_balance": game_balance }})
        elif random_list[message[2]]["name"].split(":")[0] == "jackpot":
            jackpot = database.jackpot.get({})
            jackpot_part = int(float(int(random_list[message[2]]["name"].split(":")[1].replace("%", "")) / 100) * jackpot["balance"])
            balance += jackpot_part
            database.user.update(user["_id"], {"$set": {"balance": balance}})
            database.jackpot.update(jackpot["_id"], {"$set": {"balance": jackpot["balance"] - jackpot_part}})
        emit('message', json.dumps([message[0], message[1], message[2], random_list, game_balance, balance]))
    elif message[0] == 'jackpot':
        if message[1] == 'get':
            emit('message', json.dumps([message[0], message[1], database.jackpot.get()]))
    elif message[0] == 'settings':
        if message[1] == 'get':
            emit('message', json.dumps([message[0], message[1], database.settings.get()]))
    elif message[0] == 'partner':
        if message[1] == 'get':
            emit('message', json.dumps([message[0], message[1], database.partner.search({})]))
    elif message[0] == 'task':
        if message[1] == 'get':
            emit('message', json.dumps([message[0], message[1], database.task.search({"partner_id": message[2]})]))
    
    
@socketio.on_error_default
def default_error_handler(e):
    if isinstance(e, ExpiredSignatureError):
        emit('message', json.dumps(["error", "Token has expired"]))
    elif 'Signature verification failed' in str(e):
        emit('message', json.dumps(["error", "Token has expired"]))
    else:
        print(str(e))

if __name__ == '__main__':
    socketio.run(app)
