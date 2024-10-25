import json
import time
import boto3
import botocore
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

def get_instance_public_ips(ec2_client, instance_ids):
    """Retrieve public IPs of the instances using EC2 client."""
    print("Fetching public IP addresses for instances...")
    instances = ec2_client.describe_instances(InstanceIds=instance_ids)
    ip_data = []
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            ip_data.append({
                'InstanceId': instance['InstanceId'],
                'PublicIpAddress': instance.get('PublicIpAddress', 'N/A'),
                'PrivateIpAddress': instance['PrivateIpAddress']
            })
    return ip_data


def create_security_group(ec2_client, vpc_id, region):
    """Create a security group with necessary rules for JMeter load test."""
    sg_name = "JMeterLoadTestSG"
    description = "Security group for JMeter load test instances"

    # First, check if the security group already exists in the VPC
    try:
        existing_sg = ec2_client.describe_security_groups(
            Filters=[
                {'Name': 'group-name', 'Values': [sg_name]},
                {'Name': 'vpc-id', 'Values': [vpc_id]}
            ]
        )
        
        # If a security group with the same name is found, return its GroupId
        if existing_sg['SecurityGroups']:
            security_group_id = existing_sg['SecurityGroups'][0]['GroupId']
            print(f"Security Group {sg_name} already exists with ID {security_group_id}. Reusing it.")
            return security_group_id
        else:
            print(f"No existing security group named {sg_name} found. Creating a new one.")
    
    except botocore.exceptions.ClientError as e:
        print(f"Error checking security group: {e}")
        return None

    # If no security group exists, create a new one
    try:
        # Create the security group
        response = ec2_client.create_security_group(
            GroupName=sg_name,
            Description=description,
            VpcId=vpc_id
        )
        
        security_group_id = response['GroupId']
        print(f"Security Group Created {security_group_id} in VPC {vpc_id}")

        # Add ingress rules to allow SSH and application ports
        ec2_client.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 22,
                    'ToPort': 22,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]  # SSH
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 80,
                    'ToPort': 80,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]  # HTTP
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 5000,
                    'ToPort': 5000,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]  # Application Port
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 8000,
                    'ToPort': 8000,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]  # Application Port
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 8080,
                    'ToPort': 8080,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]  # Application Port
                }
            ]
        )
        print("Ingress rules successfully added to the security group.")
        return security_group_id

    except botocore.exceptions.ClientError as e:
        print(f"Error creating security group: {e}")
        return None


def launch_instances():
    """Launch EC2 instances and wait for them to be in running state."""
    # Load default parameters from the JSON file
    defaults = load_defaults()
    
    if not defaults:
        print("No defaults loaded, cannot proceed with instance launch.")
        return

    region = defaults.get('Region', 'us-east-1')  # Default to 'us-east-1' if not specified

    # Initialize EC2 client with the specified region
    ec2_client = boto3.client('ec2', region_name=region)

    # Extract instance parameters from the defaults file
    instance_count = int(defaults.get('NumberOfInstances', 1))
    instance_type = defaults.get('InstanceType', 't3.2xlarge')
    ami_id = defaults.get('AMIId')
    subnet_id = defaults.get('SubnetId')
    ssh_key_name = defaults.get('SSHKeyName')
    vpc_id = defaults.get('VpcId')

    if not ami_id or not subnet_id or not ssh_key_name or not vpc_id:
        print("AMIId, SubnetId, SSHKeyName, and VpcId are required in launch-defaults.json.")
        return

    # Create the security group
    security_group_id = create_security_group(ec2_client, vpc_id, region)
    if not security_group_id:
        print("Failed to create or retrieve security group. Aborting instance launch.")
        return

    print(f"Launching {instance_count} EC2 instance(s) with AMI ID: {ami_id}")

    # Launch instances
    try:
        instances = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            KeyName=ssh_key_name,
            MaxCount=instance_count,
            MinCount=instance_count,
            SubnetId=subnet_id,
            SecurityGroupIds=[security_group_id],
            UserData="""#!/bin/bash
            sudo apt update -y
            sudo apt install -y git python3-venv python3-pip
            cd /home/ubuntu/
            if [ ! -d "load-test" ]; then
              mkdir load-test
            fi
            cd load-test
            git clone https://github.com/deepakgonda/jmeter-distributed-load-test.git
            cd jmeter-distributed-load-test
            python3 -m venv venv
            source venv/bin/activate
            pip install -r requirements.txt
            nohup python3 slave.py &
            """
        )

        # Collect instance IDs for tracking
        instance_ids = [instance['InstanceId'] for instance in instances['Instances']]
        print("Instances launched:", instance_ids)

        # Wait for all instances to be in 'running' state
        print("Waiting for EC2 instances to be in 'running' state...")
        ec2_client.get_waiter('instance_running').wait(InstanceIds=instance_ids)
        
        # Fetch public and private IP addresses of the instances
        ip_data = []
        instance_descriptions = ec2_client.describe_instances(InstanceIds=instance_ids)
        for reservation in instance_descriptions['Reservations']:
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

    except botocore.exceptions.ClientError as e:
        print(f"Error launching instances: {e}")


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
            launch_instances()

        elif choice == 2:
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
                continue

        elif choice == 4:
            print("Exiting the program.")
            break


if __name__ == "__main__":
    main()
