import json
import time
import boto3
import requests
import os
import subprocess
import pandas as pd
from jmeter_runner import run_jmeter_test, analyze_results

# Define paths for the generated JSON file
INSTANCE_IPS_FILE = "instance_ips.json"


def load_defaults():
    """Load default parameters from 'launch-defaults.json'."""
    defaults_file_path = os.path.join(os.getcwd(), "cloudformation", "launch-defaults.json")
    
    if os.path.exists(defaults_file_path):
        with open(defaults_file_path, 'r') as defaults_file:
            return json.load(defaults_file)
    else:
        print(f"No defaults file found at {defaults_file_path}")
        return {}

def create_cloudformation_stack():
    """Run CloudFormation template to create EC2 instances and wait for completion."""
    # Load default parameters from the JSON file
    defaults = load_defaults()
    
    if not defaults:
        print("No defaults loaded, cannot proceed with stack creation.")
        return

    # Initialize CloudFormation and EC2 clients
    cloudformation_client = boto3.client('cloudformation')
    ec2_client = boto3.client('ec2')

    # Define the CloudFormation template path within the repository
    template_file_path = os.path.join(os.getcwd(), "cloudformation", "launch-slaves.yaml")
    with open(template_file_path, 'r') as template_file:
        template_body = template_file.read()

    # Start the CloudFormation stack
    stack_name = "JMeterLoadTestStack"
    print("Creating CloudFormation stack...")
    cloudformation_client.create_stack(
        StackName=stack_name,
        TemplateBody=template_body,
        Parameters=[
            {'ParameterKey': 'VpcId', 'ParameterValue': defaults.get('VpcId')}, 
            {'ParameterKey': 'SubnetId', 'ParameterValue': defaults.get('SubnetId')},
            {'ParameterKey': 'SSHKeyName', 'ParameterValue': defaults.get('SSHKeyName')},
            {'ParameterKey': 'AMIId', 'ParameterValue': defaults.get('AMIId')},
            {'ParameterKey': 'InstanceType', 'ParameterValue': defaults.get('InstanceType')},
            {'ParameterKey': 'NumberOfInstances', 'ParameterValue': defaults.get('NumberOfInstances')},
        ],
        Capabilities=['CAPABILITY_IAM'],
    )

    # Wait for the CloudFormation stack to be created
    print("Waiting for stack creation to complete...")
    waiter = cloudformation_client.get_waiter('stack_create_complete')
    waiter.wait(StackName=stack_name)

    # Get the list of instance IDs created by the stack
    stack_resources = cloudformation_client.describe_stack_resources(StackName=stack_name)
    instance_ids = [resource['PhysicalResourceId'] for resource in stack_resources['StackResources']
                    if resource['ResourceType'] == 'AWS::EC2::Instance']

    print(f"Instances created: {instance_ids}")

    # Wait for all instances to be running
    print("Waiting for EC2 instances to be in 'running' state...")
    ec2_client.get_waiter('instance_running').wait(InstanceIds=instance_ids)

    # Collect public and private IP addresses
    instances = ec2_client.describe_instances(InstanceIds=instance_ids)
    ip_data = []
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            ip_data.append({
                'InstanceId': instance['InstanceId'],
                'PublicIpAddress': instance.get('PublicIpAddress', 'N/A'),
                'PrivateIpAddress': instance['PrivateIpAddress']
            })

    # Save IP addresses to a JSON file
    with open(INSTANCE_IPS_FILE, 'w') as ip_file:
        json.dump(ip_data, ip_file, indent=4)

    print(f"Instance IPs saved to {INSTANCE_IPS_FILE}")


def load_instance_ips():
    """Load instance IPs from the JSON file."""
    if os.path.exists(INSTANCE_IPS_FILE):
        with open(INSTANCE_IPS_FILE, 'r') as file:
            return json.load(file)
    else:
        print(f"No instance IP file found at {INSTANCE_IPS_FILE}")
        return []


def start_test_on_slaves(jmx_file):
    """Send start-test requests to all slaves using the IPs from the JSON file."""
    ips_data = load_instance_ips()
    if not ips_data:
        print("No IPs found to start tests.")
        return

    for instance in ips_data:
        ip = instance['PublicIpAddress'] if instance['PublicIpAddress'] != 'N/A' else instance['PrivateIpAddress']
        url = f"http://{ip}:5000/start-test"
        response = requests.post(url, json={"jmx_file": jmx_file})
        print(f"Started test on {ip}: {response.json()}")

def monitor_tests_on_slaves():
    """Continuously monitor the status of the test on all slave instances."""
    ips_data = load_instance_ips()
    if not ips_data:
        print("No IPs found to monitor tests.")
        return
    
    all_finished = False
    while not all_finished:
        all_finished = True
        for instance in ips_data:
            ip = instance['PublicIpAddress'] if instance['PublicIpAddress'] != 'N/A' else instance['PrivateIpAddress']
            url = f"http://{ip}:5000/check-status"
            response = requests.get(url)
            status_data = response.json()
            if status_data["status"] == "Running":
                print(f"Test still running on {ip}")
                all_finished = False
            else:
                print(f"Test completed on {ip}")
        time.sleep(10)  # Poll every 10 seconds

def collect_results_from_slaves():
    """Collect results from all slave instances using the IPs from the JSON file."""
    ips_data = load_instance_ips()
    if not ips_data:
        print("No IPs found to collect results.")
        return

    all_results = []
    for instance in ips_data:
        ip = instance['PublicIpAddress'] if instance['PublicIpAddress'] != 'N/A' else instance['PrivateIpAddress']
        url = f"http://{ip}:5000/get-results"
        response = requests.get(url)
        result_file = f"{instance['InstanceId']}_results.jtl"
        with open(result_file, 'wb') as f:
            f.write(response.content)
        print(f"Collected result from {ip}: {result_file}")
        all_results.append(result_file)

    return all_results

def analyze_collected_results(all_results):
    """Analyze results from all collected .jtl files."""
    for result_file in all_results:
        print(f"Analyzing results from {result_file}...")
        analyze_results(result_file)

def main_menu():
    """Display the main menu and get user choice."""
    print("Main Menu:")
    print("1. Run CloudFormation to launch EC2 instances")
    print("2. Run new JMeter test on all slaves")
    print("3. Analyze previous load test results")
    print("4. Exit")

    while True:
        try:
            choice = int(input("Enter your choice: "))
            if 1 <= choice <= 4:
                return choice
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")


def main():
    load_test_dir = 'load_test'  # Directory where the load test files are stored

    while True:
        choice = main_menu()

        if choice == 1:
            # Run CloudFormation stack and gather IPs
            create_cloudformation_stack()

        elif choice == 2:
            # List and choose JMX file from the 'load_test' directory
            jmx_files = [f for f in os.listdir(load_test_dir) if f.endswith('.jmx')]
            if not jmx_files:
                print("No JMX files found.")
                return

            if len(jmx_files) == 1:
                chosen_file = jmx_files[0]
            else:
                print(f"Available JMX files: {jmx_files}")
                chosen_file = input("Choose JMX file: ")

            chosen_file_path = os.path.join(load_test_dir, chosen_file)

            # Start the test on slaves with the chosen JMX file
            start_test_on_slaves(chosen_file_path)

            # Monitor tests until completion
            monitor_tests_on_slaves()

            # Collect results from all slaves
            results = collect_results_from_slaves()
            print("All results collected:", results)

            # Analyze the collected results
            analyze_collected_results(results)

        elif choice == 3:
            # List and choose JTL files for analysis from the 'load_test' directory
            jtl_files = [f for f in os.listdir(load_test_dir) if f.endswith('.jtl')]
            if jtl_files:
                analyze_results(os.path.join(load_test_dir, jtl_files[0]))
            else:
                print("No JTL files available for analysis.")

        elif choice == 4:
            print("Exiting the program.")
            break


if __name__ == "__main__":
    main()
