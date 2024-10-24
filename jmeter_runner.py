import subprocess
import os
import time
import pandas as pd

def calculate_heap_size():
    """Calculate 90% of total RAM for JMeter, rounded to the nearest GB."""
    total_ram = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    total_ram_mb = total_ram / (1024 ** 2)  # Convert to MB
    max_heap_size_mb = int(total_ram_mb * 0.9)  # 90% of total RAM
    heap_size_gb = round(max_heap_size_mb / 1024)
    heap_size_mb = heap_size_gb * 1024  # Convert back to MB
    return heap_size_mb

def run_jmeter_test(jmx_file):
    """Run the JMeter test with the specified JMX file."""
    print(f"Running JMeter test plan: {jmx_file}...")
    result_file = "results-file.jtl"

    # Clear the existing result file if it exists
    if os.path.exists(result_file):
        os.remove(result_file)

    heap_size_mb = calculate_heap_size()
    jmeter_command = [
        "jmeter", "-n", "-t", jmx_file, "-l", result_file,
        "-JheapSize=" + str(heap_size_mb)  # Set heap size
    ]

    process = subprocess.Popen(jmeter_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process  # Return process handle for status checking

def check_jmeter_status(process):
    """Check if the JMeter process is still running."""
    poll_result = process.poll()
    if poll_result is None:
        return "Running"
    else:
        return "Completed"

def analyze_results(result_file="results-file.jtl"):
    """Analyze the JMeter results file and count status codes."""
    print(f"Analyzing results from {result_file}...")
    df = pd.read_csv(result_file, sep=',', dtype={'responseCode': str})

    df['responseCode'] = pd.to_numeric(df['responseCode'], errors='coerce')

    status_counts = {
        '2xx': 0,
        '3xx': 0,
        '4xx': 0,
        '5xx': 0,
        'other': 0
    }

    other_errors = {}
    error_5xx = {}

    for index, row in df.iterrows():
        status = row['responseCode']
        response_message = row.get('responseMessage', 'No message')

        if pd.isna(status):
            status_counts['other'] += 1
            other_errors[response_message] = other_errors.get(response_message, 0) + 1
        elif 200 <= status < 300:
            status_counts['2xx'] += 1
        elif 300 <= status < 400:
            status_counts['3xx'] += 1
        elif 400 <= status < 500:
            status_counts['4xx'] += 1
        elif 500 <= status < 600:
            status_counts['5xx'] += 1
            error_5xx[response_message] = error_5xx.get(response_message, 0) + 1
        else:
            status_counts['other'] += 1
            other_errors[str(status)] = other_errors.get(str(status), 0) + 1

    print("Request status counts:")
    for key, count in status_counts.items():
        print(f"{key}: {count}")

    if status_counts['other'] > 0:
        sorted_other_errors = sorted(other_errors.items(), key=lambda item: item[1], reverse=True)
        top_other_errors = sorted_other_errors[:3]
        print("Top 3 other errors:")
        for error, count in top_other_errors:
            print(f"Error: {error}: {count}")

    if status_counts['5xx'] > 0:
        sorted_5xx_errors = sorted(error_5xx.items(), key=lambda item: item[1], reverse=True)
        top_5xx_errors = sorted_5xx_errors[:3]
        print("Top 3 errors for 5xx status codes:")
        for error, count in top_5xx_errors:
            print(f"Error: {error}: {count}")

def get_latest_results_file():
    """Return the path to the latest `results-file.jtl`."""
    result_file = "results-file.jtl"
    if os.path.exists(result_file):
        return result_file
    else:
        return None
