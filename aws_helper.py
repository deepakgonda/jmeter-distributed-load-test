import json
import os
import boto3
import botocore
import time

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


def get_region():
    """Load region from defaults or use a default region."""
    defaults = load_defaults()
    return defaults.get("Region", "us-east-1")  # Default to 'us-east-1' if not specified


def get_next_instance_index():
    """Get the next instance index from the INSTANCE_IPS_FILE."""
    if os.path.exists(INSTANCE_IPS_FILE):
        with open(INSTANCE_IPS_FILE, 'r') as ip_file:
            ip_data = json.load(ip_file)
            return len(ip_data) + 1  # Next index starts from len + 1
    return 1


def get_instance_public_ips(ec2_client, instance_ids):
    """Retrieve public and private IPs of the instances using EC2 client."""
    print("Fetching public IP addresses for instances...")
    instances = ec2_client.describe_instances(InstanceIds=instance_ids)
    ip_data = []
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            ip_data.append({
                'InstanceId': instance['InstanceId'],
                'PublicIpAddress': instance.get('PublicIpAddress', 'N/A'),
                'PrivateIpAddress': instance.get('PrivateIpAddress', 'N/A')
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


def launch_instances(instance_count):
    """Launch EC2 instances and wait for them to be in running state."""
    defaults = load_defaults()
    if not defaults:
        print("No defaults loaded, cannot proceed with instance launch.")
        return

    region = defaults.get('Region', 'us-east-1')

    region = get_region()
    ec2_client = boto3.client('ec2', region_name=region)

    # instance_count = int(defaults.get('NumberOfInstances', 1))
    instance_type = defaults.get('InstanceType', 't3.2xlarge')
    ami_id = defaults.get('AMIId')
    subnet_id = defaults.get('SubnetId')
    ssh_key_name = defaults.get('SSHKeyName')
    vpc_id = defaults.get('VpcId')

    if not ami_id or not subnet_id or not ssh_key_name or not vpc_id:
        print("AMIId, SubnetId, SSHKeyName, and VpcId are required in launch-defaults.json.")
        return

    security_group_id = create_security_group(ec2_client, vpc_id, region)
    if not security_group_id:
        print("Failed to create or retrieve security group. Aborting instance launch.")
        return

    next_index = get_next_instance_index()

    print(f"Launching {instance_count} EC2 instance(s) with AMI ID: {ami_id}")

    try:
        instances = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            KeyName=ssh_key_name,
            MaxCount=instance_count,
            MinCount=instance_count,
            SubnetId=subnet_id,
            SecurityGroupIds=[security_group_id],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [
                    {'Key': 'Name', 'Value': f"Ubuntu-Jmeter-Load-Test-Slave-{next_index + i}"}
                    for i in range(instance_count)
                ]
            }],
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

        instance_ids = [instance['InstanceId'] for instance in instances['Instances']]
        print("Instances launched:", instance_ids)

        print("Waiting for EC2 instances to be in 'running' state...")
        ec2_client.get_waiter('instance_running').wait(InstanceIds=instance_ids)

        ip_data = get_instance_public_ips(ec2_client, instance_ids)

        with open(INSTANCE_IPS_FILE, 'w') as ip_file:
            json.dump(ip_data, ip_file, indent=4)

        print(f"Instance IPs saved to {INSTANCE_IPS_FILE}")

    except botocore.exceptions.ClientError as e:
        print(f"Error launching instances: {e}")


def find_existing_instances():
    """Find, filter, and ensure instances with the Name tag 'Ubuntu-Jmeter-Load-Test-Slave-{{i}}' are running."""
    region = get_region()
    ec2_client = boto3.client('ec2', region_name=region)

    try:
        # Fetch all instances with the specified Name tag
        instances = ec2_client.describe_instances(
            Filters=[{
                'Name': 'tag:Name',
                'Values': ['Ubuntu-Jmeter-Load-Test-Slave-*']
            }]
        )
        
        # Prepare lists to track instance states
        instance_ids = []
        stopped_instances = []
        running_instances = []
        
        # Classify each instance based on its state, ignoring terminated instances
        for reservation in instances['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                state = instance['State']['Name']
                
                if state == 'terminated':
                    continue  # Skip terminated instances entirely
                elif state == 'stopped':
                    stopped_instances.append(instance_id)
                elif state == 'running':
                    running_instances.append(instance_id)
                    instance_ids.append(instance_id)  # Collect running instance IDs
                else:
                    instance_ids.append(instance_id)  # Include pending instances as well

        print(f"Found {len(instance_ids)} instances with tag 'Ubuntu-Jmeter-Load-Test-Slave-*'.")
        print(f"Instances running: {len(running_instances)}, stopped: {len(stopped_instances)}")
        
        # Start stopped instances
        if stopped_instances:
            print(f"Starting {len(stopped_instances)} stopped instances...")
            ec2_client.start_instances(InstanceIds=stopped_instances)
            instance_ids.extend(stopped_instances)

        # Wait for all instances (running + started) to reach the running state, if any instances are left to wait for
        if instance_ids:
            print("Waiting for all instances to be in 'running' state...")
            ec2_client.get_waiter('instance_running').wait(InstanceIds=instance_ids)

            # Re-fetch IP data now that all instances should be running
            ip_data = get_instance_public_ips(ec2_client, instance_ids)
        else:
            print("No running or stopped instances found to wait for.")
            ip_data = []

        # Load existing IP data from file and filter out terminated instances
        if os.path.exists(INSTANCE_IPS_FILE):
            with open(INSTANCE_IPS_FILE, 'r') as f:
                existing_ips = json.load(f)
            
            # Remove any instances in the file that no longer exist or are terminated
            updated_ips = [entry for entry in existing_ips if entry['InstanceId'] in instance_ids]
            updated_ips.extend([ip for ip in ip_data if ip['InstanceId'] not in {e['InstanceId'] for e in updated_ips}])
            
        else:
            # No existing file, so all IP data is new
            updated_ips = ip_data

        # Write the updated list to INSTANCE_IPS_FILE
        with open(INSTANCE_IPS_FILE, 'w') as ip_file:
            json.dump(updated_ips, ip_file, indent=4)

        print(f"Updated {INSTANCE_IPS_FILE} with a total of {len(updated_ips)} instances, all in 'running' state.")

    except botocore.exceptions.ClientError as e:
        print(f"Error fetching existing instances: {e}")
    except botocore.exceptions.WaiterError as e:
        print(f"Error waiting for instances to start: {e}")



def terminate_instances():
    """Terminate all instances with the Name tag 'Ubuntu-Jmeter-Load-Test-Slave-{{i}}' and clear the INSTANCE_IPS_FILE."""
    region = get_region()
    ec2_client = boto3.client('ec2', region_name=region)
    try:
        instances = ec2_client.describe_instances(
            Filters=[{
                'Name': 'tag:Name',
                'Values': ['Ubuntu-Jmeter-Load-Test-Slave-*']
            }]
        )
        instance_ids = [i['InstanceId'] for r in instances['Reservations'] for i in r['Instances']]
        if instance_ids:
            print(f"Terminating instances: {instance_ids}")
            ec2_client.terminate_instances(InstanceIds=instance_ids)
            ec2_client.get_waiter('instance_terminated').wait(InstanceIds=instance_ids)
            print(f"Instances terminated: {instance_ids}")

            if os.path.exists(INSTANCE_IPS_FILE):
                os.remove(INSTANCE_IPS_FILE)
                print(f"{INSTANCE_IPS_FILE} cleared.")
        else:
            print("No instances found to terminate.")
    except botocore.exceptions.ClientError as e:
        print(f"Error terminating instances: {e}")
