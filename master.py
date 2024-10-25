import os
import json
import requests
import time
from aws_helper import (
    find_existing_instances,
    launch_instances,
    terminate_instances,
)
from jmeter_runner import (
    run_jmeter_test,
    check_jmeter_status,
    get_latest_results_file,
    analyze_results,
)

INSTANCE_IPS_FILE = "instance_ips.json"


def main_menu():
    """Display the main menu and get user choice."""
    print("\n\n\n")
    print("Main Menu:")
    print("1. Find and update existing instances in INSTANCE_IPS_FILE")
    print("2. Launch EC2 instances")
    print("3. Sync .jmx files to all slaves")
    print("4. Run new JMeter test on all slaves")
    print("5. Analyze previous load test results")
    print("6. Terminate all instances and clear INSTANCE_IPS_FILE")
    print("7. Check health of all slave instances")  # New menu option
    print("8. Exit")

    while True:
        try:
            choice = int(input("Enter your choice: "))
            if 1 <= choice <= 8:
                return choice
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")


def check_all_slaves_health():
    """Check if all slave instances are up and running."""
    if not os.path.exists(INSTANCE_IPS_FILE):
        print("INSTANCE_IPS_FILE not found. Please find or launch instances first.")
        return False

    # Load instance IPs
    with open(INSTANCE_IPS_FILE, 'r') as file:
        instance_ips = json.load(file)

    all_healthy = True
    for instance in instance_ips:
        ip = instance['PublicIpAddress'] if instance['PublicIpAddress'] != 'N/A' else instance['PrivateIpAddress']
        url = f"http://{ip}:5000/health"
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"Slave {ip} is up and running.")
            else:
                print(f"Slave {ip} is unreachable.")
                all_healthy = False
        except requests.exceptions.RequestException as e:
            print(f"Failed to reach slave {ip}: {e}")
            all_healthy = False

    return all_healthy


def start_test_on_all_slaves(jmx_file):
    """Start the JMeter test on all slaves if they are all healthy."""
    if not check_all_slaves_health():
        print("Not all slaves are up and running. Aborting test.")
        return

    # Start test only if all slaves are healthy
    process = run_jmeter_test(jmx_file)

    # Monitor the process until it completes
    while True:
        status = check_jmeter_status(process)
        if status == "Running":
            print("Test is still running...")
        else:
            print("Test has completed.")
            break
        time.sleep(10)  # Poll every 10 seconds

    # Analyze results after the test is complete
    result_file = get_latest_results_file()
    if result_file:
        analyze_results(result_file)
    else:
        print("No results file found after the test.")


def sync_jmx_files():
    """Sync all .jmx files from master to all slaves."""
    if not os.path.exists(INSTANCE_IPS_FILE):
        print("INSTANCE_IPS_FILE not found. Please find or launch instances first.")
        return

    # Load instance IPs
    with open(INSTANCE_IPS_FILE, 'r') as file:
        instance_ips = json.load(file)

    # Check all slaves health...
    all_healthy = check_all_slaves_health()
    if not all_healthy:
        print("Some of slaves are not healthy, cannot sync")
        return
    
    load_test_dir = 'load_test'
    jmx_files = [f for f in os.listdir(load_test_dir) if f.endswith('.jmx')]
    if not jmx_files:
        print("No .jmx files found in the load_test directory.")
        return

    # Loop through each slave instance and send each .jmx file
    for instance in instance_ips:
        ip = instance['PublicIpAddress'] if instance['PublicIpAddress'] != 'N/A' else instance['PrivateIpAddress']
        for jmx_file in jmx_files:
            file_path = os.path.join(load_test_dir, jmx_file)
            with open(file_path, 'rb') as f:
                url = f"http://{ip}:5000/sync-jmx"
                try:
                    response = requests.post(url, files={'file': f})
                    print(f"Syncing {jmx_file} to {ip}: {response.json()}")
                except requests.exceptions.RequestException as e:
                    print(f"Failed to sync {jmx_file} to {ip}: {e}")


def analyze_previous_results():
    """Analyze previously saved results if they exist."""
    result_file = get_latest_results_file()
    if result_file:
        analyze_results(result_file)
    else:
        print("No previous results available for analysis.")


def main():
    load_test_dir = 'load_test'  # Directory where the load test files are stored

    while True:
        choice = main_menu()

        if choice == 1:
            find_existing_instances()

        elif choice == 2:
            try:
                instance_count = int(input("Enter the number of instances to launch: "))
                if instance_count <= 0:
                    print("Please enter a positive number.")
                    continue
                launch_instances(instance_count)
            except ValueError:
                print("Invalid input. Please enter a valid number.")

        elif choice == 3:
            # Sync .jmx files from master to all slaves
            sync_jmx_files()

        elif choice == 4:
            # List and choose JMX file from the 'load_test' directory
            jmx_files = [f for f in os.listdir(load_test_dir) if f.endswith('.jmx')]
            if not jmx_files:
                print("No JMX files found. Returning to the main menu.")
                continue

            if len(jmx_files) == 1:
                chosen_file = jmx_files[0]
            else:
                print(f"Available JMX files: {jmx_files}")
                chosen_file = input("Choose JMX file: ")

            chosen_file_path = os.path.join(load_test_dir, chosen_file)

            # Start the test on all slaves
            start_test_on_all_slaves(chosen_file_path)

        elif choice == 5:
            # Analyze previous load test results
            analyze_previous_results()

        elif choice == 6:
            terminate_instances()

        elif choice == 7:
            # Check health of all slave instances
            check_all_slaves_health()

        elif choice == 8:
            print("Exiting the program.")
            break


if __name__ == "__main__":
    main()
