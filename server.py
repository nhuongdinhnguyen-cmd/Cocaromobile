import asyncio
import random
import string
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="Caro Online")
rooms = {}
INDEX = Path(__file__).with_name("index.html")
SIZE = 15
WIN_COUNT = 5


def new_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        if code not in rooms:
            return code


def fresh_board():
    return [["" for _ in range(SIZE)] for _ in range(SIZE)]


def has_won(board, row, col, mark):
    for dr, dc in ((1, 0), (0, 1), (1, 1), (1, -1)):
        count = 1
        for sign in (-1, 1):
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < SIZE and 0 <= c < SIZE and board[r][c] == mark:
                count += 1
                r += dr * sign
                c += dc * sign
        if count >= WIN_COUNT:
            return True
    return False


def state(room):
    return {
        "board": room["board"], "turn": room["turn"],
        "scores": room["scores"], "game_over": room["game_over"],
        "winner": room["winner"], "players": len(room["players"]),
        "last_move": room["last_move"],
    }


async def send_room(room, payload):
    stale = []
    for mark, ws in list(room["players"].items()):
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(mark)
    for mark in stale:
        room["players"].pop(mark, None)


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {"status": "ok", "game": "caro-online"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    room_code = None
    mark = None
    try:
        request = await ws.receive_json()
        action = request.get("action")
        if action == "create":
            room_code = new_code()
            mark = "X"
            rooms[room_code] = {
                "players": {"X": ws}, "board": fresh_board(), "turn": "X",
                "scores": {"X": 0, "O": 0, "draw": 0, "games": 0},
                "game_over": False, "winner": None, "last_move": None,
            }
            room = rooms[room_code]
            await ws.send_json({"type": "created", "room_code": room_code, "mark": mark, "state": state(room)})
        elif action == "join":
            room_code = str(request.get("room_code", "")).strip().upper()
            room = rooms.get(room_code)
            if not room:
                await ws.send_json({"type": "error", "message": "Không tìm thấy phòng. Hãy kiểm tra mã phòng hoặc tạo phòng mới."})
                await ws.close()
                return
            if len(room["players"]) >= 2 or "O" in room["players"]:
                await ws.send_json({"type": "error", "message": "Phòng đã đủ 2 người chơi."})
                await ws.close()
                return
            mark = "O"
            room["players"][mark] = ws
            await ws.send_json({"type": "joined", "room_code": room_code, "mark": mark, "state": state(room)})
            await send_room(room, {"type": "state", "state": state(room), "message": "Đã có đủ 2 người chơi. X đi trước."})
        else:
            await ws.send_json({"type": "error", "message": "Yêu cầu không hợp lệ."})
            await ws.close()
            return

        room = rooms[room_code]
        while True:
            request = await ws.receive_json()
            action = request.get("action")
            if action == "move":
                if mark not in room["players"]:
                    await ws.send_json({"type": "error", "message": "Bạn không còn ở trong phòng."})
                    continue
                if len(room["players"]) < 2:
                    await ws.send_json({"type": "error", "message": "Đang chờ người chơi thứ hai vào phòng."})
                    continue
                if room["game_over"] or room["turn"] != mark:
                    continue
                try:
                    r, c = int(request.get("row")), int(request.get("col"))
                except (TypeError, ValueError):
                    continue
                if not (0 <= r < SIZE and 0 <= c < SIZE) or room["board"][r][c]:
                    continue
                room["board"][r][c] = mark
                room["last_move"] = [r, c]
                if has_won(room["board"], r, c, mark):
                    room["game_over"] = True
                    room["winner"] = mark
                    room["scores"][mark] += 1
                    room["scores"]["games"] += 1
                    msg = f"Quân {mark} thắng ván này!"
                elif all(cell for row in room["board"] for cell in row):
                    room["game_over"] = True
                    room["winner"] = "draw"
                    room["scores"]["draw"] += 1
                    room["scores"]["games"] += 1
                    msg = "Ván cờ hòa!"
                else:
                    room["turn"] = "O" if mark == "X" else "X"
                    msg = ""
                await send_room(room, {"type": "state", "state": state(room), "message": msg})
            elif action == "new_round":
                if len(room["players"]) >= 2:
                    room["board"] = fresh_board()
                    room["turn"] = "X"
                    room["game_over"] = False
                    room["winner"] = None
                    room["last_move"] = None
                    await send_room(room, {"type": "state", "state": state(room), "message": "Ván mới bắt đầu. X đi trước."})
            elif action == "leave":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print("WebSocket error:", repr(exc))
    finally:
        if room_code and room_code in rooms and mark:
            room = rooms[room_code]
            if room["players"].get(mark) is ws:
                room["players"].pop(mark, None)
                if room["players"]:
                    await send_room(room, {"type": "state", "state": state(room), "message": "Đối thủ đã rời phòng. Có thể vào lại bằng mã phòng."})
                else:
                    rooms.pop(room_code, None)
