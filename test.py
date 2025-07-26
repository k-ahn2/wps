import socket, json, time, uuid
import threading

def listen():
    while True:
        try:
            data = sock.recv(1024)
            if not data:
                break
            print(f"Received: {data.decode()}")
        except Exception as e:
            print(f"Error receiving data: {e}")
            break

def convert_json_ready_to_send(json_object):
    return json.dumps(json_object, separators=(',', ':')) + '\r\n'

env_source = open("env.json")
env = json.load(env_source)
env_source.close()

TEST_CALLSIGN = "T3EST"

sock = socket.socket()
sock.connect(('127.0.0.1', env['socketTcpPort']))

print("Connected to server")
time.sleep(1)
threading.Thread(target=listen, daemon=True).start()
time.sleep(3)
print(f"Sending callsign: {TEST_CALLSIGN}")
sock.sendall(f'{TEST_CALLSIGN}\r\n'.encode())
time.sleep(3)

test_message = {
   "t": "m",
   "_id": str(uuid.uuid4()),
   "fc": TEST_CALLSIGN,
   "tc": "T4EST",
   "m": "Hello, this is a test message",
   "ts": time.time(),
}
print(f'Sending message: {str(test_message)}')
sock.sendall(convert_json_ready_to_send(test_message).encode())

time.sleep(3)
print("Closing socket")