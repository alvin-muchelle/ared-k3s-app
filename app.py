from flask import Flask, jsonify
import socket, datetime
app = Flask(__name__)

@app.route("/")
def root():
    return jsonify({
        "host": socket.gethostname(),
        "time": datetime.datetime.utcnow().isoformat() + "Z"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
