# Ansible Beginner's Practice Guide
*Safe, No-Risk Learning Path for SentinelCam Infrastructure*

## ⚡ Quick Start - Try This Right Now!

**Want to try Ansible immediately? Here's a 30-second test:**

```bash
# SSH to buzz, then run this completely safe command:
cd /home/pi/ansible
ansible all -i inventory/hosts -m ping
```

This will test connectivity to all your nodes and show you Ansible in action. It makes no changes - just tests if Ansible can reach your infrastructure.

**Expected output:** Green "SUCCESS" messages from each node.

---

## 🎯 Learning Philosophy: Start Safe, Build Confidence

This guide follows a **zero-risk progression** - we'll start with completely safe, read-only operations and gradually build up your Ansible skills. Every exercise is designed to be **reversible** and **non-destructive**.

## 📋 Prerequisites

- SSH access to your infrastructure (bastion → data1 → buzz)
- Basic Linux command line familiarity
- Your enhanced code transmission pipeline (already installed)

## 🚦 Safety Levels

### 🟢 **Level 1: READ-ONLY** (Start Here!)
- Information gathering only
- No system changes
- Cannot break anything

### 🟡 **Level 2: SAFE CHANGES**
- Temporary files only
- Easily reversible
- Low impact operations

### 🟠 **Level 3: MANAGED CHANGES**
- Real configuration changes
- With automatic backups
- Using your rollback system

---

## 🟢 Level 1: Read-Only Ansible Operations

### Exercise 1.1: Your First Ansible Command - Check Connectivity

Let's start with the most basic Ansible command - just checking if your nodes are reachable:

```bash
# From buzz host:
ansible all -i inventory/hosts -m ping
```

**What this does:**
- `all` = target all hosts in inventory
- `-i inventory/hosts` = use your inventory file
- `-m ping` = use the ping module (just tests connectivity)
- **SAFETY**: This only tests SSH connectivity, makes no changes

### Exercise 1.2: Gather System Information

```bash
# Get basic facts about all your nodes
ansible all -i inventory/hosts -m setup --tree /tmp/facts

# View what we collected (completely safe to look at)
ls /tmp/facts/
cat /tmp/facts/[any-hostname]
```

**What this teaches:**
- How Ansible gathers system information
- Understanding of your infrastructure inventory
- Reading Ansible output

### Exercise 1.3: Check What's Running

```bash
# See what services are running (read-only)
ansible all -i inventory/hosts -m shell -a "systemctl list-units --type=service --state=running"

# Check disk usage across all nodes
ansible all -i inventory/hosts -m shell -a "df -h"

# Check memory usage
ansible all -i inventory/hosts -m shell -a "free -h"
```

**SAFETY**: All read-only commands, just gathering information.

### Exercise 1.4: Explore Your Inventory

```bash
# List all hosts Ansible knows about
ansible all -i inventory/hosts --list-hosts

# Check if specific groups exist
ansible imagenode -i inventory/hosts --list-hosts
ansible camwatcher -i inventory/hosts --list-hosts

# Test connectivity to specific groups
ansible imagenode -i inventory/hosts -m ping
```

---

## 🟡 Level 2: Safe Changes (Temporary Files & Low-Risk Operations)

### Exercise 2.1: Create Temporary Files

```bash
# Create a harmless test file on all nodes
ansible all -i inventory/hosts -m file -a "path=/tmp/ansible-test-$(date +%s).txt state=touch"

# List your test files
ansible all -i inventory/hosts -m shell -a "ls -la /tmp/ansible-test-*"

# Clean up (remove the test files)
ansible all -i inventory/hosts -m shell -a "rm -f /tmp/ansible-test-*"
```

**What this teaches:**
- Basic file operations with Ansible
- How to target all nodes with one command  
- Clean-up operations

### Exercise 2.2: Use Variables and Templates

Create a simple template to practice:

```bash
# Create a test template file on buzz
cat > /tmp/test-template.j2 << 'EOF'
Hello from Ansible!
This file was created on: {{ ansible_hostname }}
System uptime: {{ ansible_uptime_seconds }} seconds
Date: {{ ansible_date_time.date }}
EOF

# Deploy this harmless template to all nodes
ansible all -i inventory/hosts -m template -a "src=/tmp/test-template.j2 dest=/tmp/ansible-info.txt"

# Check what was created
ansible all -i inventory/hosts -m shell -a "cat /tmp/ansible-info.txt"

# Clean up
ansible all -i inventory/hosts -m file -a "path=/tmp/ansible-info.txt state=absent"
```

### Exercise 2.3: Practice with Conditionals

```bash
# Only create files on specific types of nodes
ansible all -i inventory/hosts -m file -a "path=/tmp/imagenode-marker.txt state=touch" --limit "imagenode"

ansible all -i inventory/hosts -m file -a "path=/tmp/camwatcher-marker.txt state=touch" --limit "camwatcher"

# Check what was created where
ansible all -i inventory/hosts -m shell -a "ls -la /tmp/*-marker.txt 2>/dev/null || echo 'No marker files'"

# Clean up
ansible all -i inventory/hosts -m shell -a "rm -f /tmp/*-marker.txt"
```

---

## 🎓 Practice Exercises with Your Real Playbooks

### Exercise 3.1: Check Your Existing Playbooks (Safe)

```bash
# List your current playbooks
ls -la /home/pi/ansible/*.yml

# Use ansible-playbook in check mode (NO CHANGES MADE)
ansible-playbook -i inventory/hosts imagenode.yaml --check --diff

# This shows you what WOULD change without actually changing it
ansible-playbook -i inventory/hosts camwatcher.yaml --check --diff
```

**SAFETY**: `--check` mode never makes changes, only shows what would happen.

### Exercise 3.2: Practice with Limits (Target Specific Nodes)

```bash
# Target only one specific node for testing
ansible-playbook -i inventory/hosts imagenode.yaml --check --limit "imagenode1.local"

# Target only imagenode group
ansible-playbook -i inventory/hosts imagenode.yaml --check --limit "imagenode"

# Test on a single camwatcher node
ansible-playbook -i inventory/hosts camwatcher.yaml --check --limit "camwatcher1.local"
```

---

## 🛡️ Building Confidence with Your Rollback System

### Exercise 4.1: Practice Creating Manual Backups

```bash
# Create a manual backup before any real changes
/home/pi/ansible/scripts/create-rollback-checkpoint.sh "practice-session-$(date +%Y%m%d-%H%M)"

# List your available rollback points
ls -la /home/pi/ansible/rollback/
```

### Exercise 4.2: Test Your Rollback System (With Temporary Changes)

```bash
# 1. Create a backup checkpoint
/home/pi/ansible/scripts/create-rollback-checkpoint.sh "before-practice-test"

# 2. Make a harmless change to practice rollback
ansible all -i inventory/hosts -m file -a "path=/tmp/practice-change.txt state=touch"

# 3. Verify the change exists
ansible all -i inventory/hosts -m shell -a "ls -la /tmp/practice-change.txt"

# 4. Practice rolling back (this removes the test file)
ansible all -i inventory/hosts -m file -a "path=/tmp/practice-change.txt state=absent"

# 5. Verify rollback worked
ansible all -i inventory/hosts -m shell -a "ls -la /tmp/practice-change.txt 2>/dev/null || echo 'File removed - rollback successful'"
```

---

## 📚 Understanding Your SentinelCam Playbooks

### Exercise 5.1: Analyze Your Playbooks (Learning)

```bash
# Look at the structure of your imagenode playbook
head -20 /home/pi/ansible/imagenode.yaml

# Check what tasks are defined
grep -n "name:" /home/pi/ansible/imagenode.yaml

# Look at your inventory structure
cat /home/pi/ansible/inventory/hosts

# Check for any group variables
ls -la /home/pi/ansible/inventory/group_vars/ 2>/dev/null || echo "No group vars directory"
```

### Exercise 5.2: Understand Task Flow

```bash
# Use ansible-playbook with verbose mode to see task flow
ansible-playbook -i inventory/hosts imagenode.yaml --check --diff -v

# Even more detailed output
ansible-playbook -i inventory/hosts imagenode.yaml --check --diff -vv
```

---

## 🎯 When You're Ready for Real Changes

### Before Making ANY Real Changes:

1. **Always create a rollback checkpoint:**
   ```bash
   /home/pi/ansible/scripts/create-rollback-checkpoint.sh "before-$(date +%Y%m%d-%H%M)"
   ```

2. **Use check mode first:**
   ```bash
   ansible-playbook -i inventory/hosts [playbook] --check --diff
   ```

3. **Limit to one node first:**
   ```bash
   ansible-playbook -i inventory/hosts [playbook] --limit "node1.local"
   ```

4. **Have your rollback command ready:**
   ```bash
   # Know how to rollback before you start
   /home/pi/ansible/scripts/rollback-to-checkpoint.sh [checkpoint-name]
   ```

---

## 🚀 Graduation: Your First Real Deployment

When you're ready to make real changes:

1. **Use your enhanced pipeline** (it has all the safety features built-in)
2. **Start with a single imagenode** for your first real deployment
3. **Use the health monitoring** to verify success

```bash
# Your safe deployment command:
cd /home/pi/ansible
./scripts/deployment-health-monitor.sh --status-only
ansible-playbook -i inventory/hosts imagenode.yaml --limit "imagenode1.local"
./scripts/deployment-health-monitor.sh
```

---

## 🆘 Emergency Commands (Keep These Handy)

```bash
# Stop any running playbook
Ctrl+C  (then Ctrl+C again if needed)

# Quick rollback to last checkpoint
/home/pi/ansible/scripts/rollback-to-last-checkpoint.sh

# Check system health
/home/pi/ansible/scripts/deployment-health-monitor.sh

# See what changed recently  
git log --oneline -10
```

---

## 🎓 Next Steps After This Guide

1. Practice each level until comfortable
2. Read through your actual playbook files to understand them
3. Make your first real deployment to a single node
4. Gradually expand to more nodes
5. Learn to write your own simple playbooks

Remember: **The best way to learn Ansible is by doing, but start safe!**
