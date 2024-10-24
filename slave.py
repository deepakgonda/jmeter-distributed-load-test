from flask import Flask, request, jsonify, send_file
import os
import threading
from jmeter_runner import run_jmeter_test, check_jmeter_status, get_latest_results_file

app = Flask(__name__)
jmeter_process = None  # Global variable to store the JMeter process handle

@app.route('/start-test', methods=['POST'])
def start_test():
    """Receive a command from the master to start the JMeter test."""
    global jmeter_process
    jmx_file = request.json.get("jmx_file")

    if jmeter_process is not None and check_jmeter_status(jmeter_process) == "Running":
        return jsonify({"status": "JMeter test already running"}), 400

    # Start the JMeter test in a separate thread
    def run_test():
        global jmeter_process
        jmeter_process = run_jmeter_test(jmx_file)

    thread = threading.Thread(target=run_test)
    thread.start()

    return jsonify({"status": "Test started on slave"}), 200

@app.route('/check-status', methods=['GET'])
def check_status():
    """Check the status of the JMeter process."""
    global jmeter_process
    if jmeter_process is None:
        return jsonify({"status": "No test running"}), 200

    status = check_jmeter_status(jmeter_process)
    return jsonify({"status": status}), 200

@app.route('/get-results', methods=['GET'])
def get_results():
    """Serve the latest `results-file.jtl`."""
    result_file = get_latest_results_file()
    if result_file is not None:
        return send_file(result_file, as_attachment=True)
    else:
        return jsonify({"error": "No results available"}), 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)  # Open to the network
