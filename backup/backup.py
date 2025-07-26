import sqlite3, json
import datetime, sys

BACKUP_SUBFOLDER = "backups"

# Environment Variables
env_source = open(f"../env.json")
env = json.load(env_source)
env_source.close()
DB_FILENAME = env['dbFilename']

if len(sys.argv) == 2:
    DATE_PARAM = sys.argv[1]
else:
    now = datetime.datetime.now()
    DATE_PARAM = now.strftime("%d-%m-%Y_%H-%M-%S")

print(DATE_PARAM)

db = sqlite3.connect(f"../{DB_FILENAME}")
cursor = db.cursor()

BACKUP_FILENAME = f"wps_backup_{DATE_PARAM}.json"

print(f"Backing up database {DB_FILENAME} to {BACKUP_FILENAME}" )

cursor.execute('''SELECT user FROM users ORDER BY id DESC''')
users_result = [i[0] for i in cursor]
print(f"Users: {len(users_result)}")

cursor.execute('''SELECT message FROM messages ORDER BY id DESC''')
messages_result = [i[0] for i in cursor]
print(f"Messages: {len(messages_result)}")

cursor.execute('''SELECT post FROM posts ORDER BY id DESC''')
posts_result = [i[0] for i in cursor]
print(f"Posts: {len(posts_result)}")

backup = {
    "users": [json.loads(i) for i in users_result],
    "messages": [json.loads(i) for i in messages_result],
    "posts": [json.loads(i) for i in posts_result]
}

backup_file = open(f"{BACKUP_SUBFOLDER}/{BACKUP_FILENAME}", "w")
backup_file.write(json.dumps(backup, indent=4))
backup_file.flush()
