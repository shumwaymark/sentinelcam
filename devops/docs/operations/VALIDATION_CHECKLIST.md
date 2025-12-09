# Pre-Deployment Validation Checklist

Use this checklist before deploying to ensure multi-site configuration is correct.

## 1. Inventory Structure Validation

### Check Required Groups

```bash
ansible-inventory -i inventory/production.yaml --graph
```

**Expected output should include:**
- `@all`
  - `@sentinelcam_nodes`
  - `@infrastructure`
  - `@datasinks`
  - `@outposts`

### Verify All Hosts Have Required Variables

```bash
ansible-inventory -i inventory/production.yaml --list | jq '.[] | select(.ansible_host)'
```

**Each host must have:**
- `ansible_host` - IP address
- `node_role` - Role identifier
- Group membership (`sentinelcam_nodes`, `infrastructure`, etc.)

---

## 2. Site Variable Validation

### Check Site Variables are Set

```bash
ansible all -i inventory/production.yaml -m debug -a "var=sentinelcam_site_name" --limit localhost
ansible all -i inventory/production.yaml -m debug -a "var=sentinelcam_bastion_hostname" --limit localhost
```

**Expected:**
- `sentinelcam_site_name` should return a value (e.g., "chandler")
- `sentinelcam_bastion_hostname` should match your bastion hostname

### Verify Health Check Hosts Exist

```bash
ansible-inventory -i inventory/production.yaml --host data1
ansible-inventory -i inventory/production.yaml --host datasink2  # For site 2
```

**Expected:**
- Returns host details
- If host doesn't exist, update `sentinelcam_health_check_hosts`

---

## 3. DNS Generation Preview

### Check What DNS Entries Will Be Generated

Create a test playbook: `test-dns-preview.yaml`

```yaml
---
- hosts: infrastructure
  gather_facts: no
  tasks:
    - name: Preview DNS entries
      debug:
        msg: |
          {% for group in bastion_dnsmasq.inventory_groups | default(['sentinelcam_nodes']) %}
          {% if group in groups %}
          DNS entries from group: {{ group }}
          {% for host in groups[group] %}
          - {{ host }} -> {{ hostvars[host].ansible_host }}
          {% endfor %}
          {% endif %}
          {% endfor %}
```

```bash
ansible-playbook -i inventory/production.yaml test-dns-preview.yaml
```

**Verify:**
- All expected hosts appear
- IP addresses are correct
- No duplicates or missing entries

---

## 4. Hostname Assertion Test

### Test Bastion Hostname Check

```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml --tags always --check
```

**Expected:**
- "Confirmed running on bastion host: chandler-gate at site chandler"
- If fails, check `sentinelcam_bastion_hostname` matches inventory

### Test on Wrong Host (Should Fail)

```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml --limit data1 --tags always --check
```

**Expected:**
- Should fail with assertion error
- "This playbook should only run on the bastion host"

---

## 5. Variable Precedence Check

### Check Where Variables Come From

```bash
ansible all -i inventory/production.yaml -m debug -a "var=sentinelcam_site_name" --limit chandler-gate -v
```

**Look for in output:**
- Shows variable source (group_vars/all/site.yaml or inventory vars)
- Value should match expected site name

### Override Test

```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml \
  -e "sentinelcam_site_name=test" \
  --tags always --check
```

**Expected:**
- Should show "site test" in output
- Confirms variable can be overridden

---

## 6. Health Check Target Validation

### Verify Health Check Targets Resolve

```bash
ansible infrastructure -i inventory/production.yaml -m debug -a \
  "msg='Internal network target: {{ hostvars[sentinelcam_health_check_hosts.internal_network].ansible_host }}'"
```

**Expected:**
- Returns IP address
- No "undefined variable" errors

### Test Connectivity to Health Check Host

```bash
ansible infrastructure -i inventory/production.yaml -m ping \
  -a "{{ hostvars[sentinelcam_health_check_hosts.internal_network].ansible_host }}"
```

**Expected:**
- Ping succeeds
- Confirms target is reachable

---

## 7. Full Deployment Dry Run

### Check Mode Deployment

```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml --check --diff
```

**Verify:**
- No syntax errors
- All tasks show reasonable changes
- DNS configuration looks correct
- No unexpected modifications

### Check DNS Template Output

```bash
ansible-playbook -i inventory/production.yaml playbooks/deploy-bastion.yaml \
  --tags dnsmasq --check --diff
```

**Review:**
- DNS entries in diff output
- All expected hosts present
- Correct IP addresses

---

## 8. Second Site Validation (If Applicable)

If deploying to a second site, repeat all above checks with the new inventory:

```bash
ansible-inventory -i inventory/site2.yaml --graph
ansible-inventory -i inventory/site2.yaml --list
ansible-playbook -i inventory/site2.yaml playbooks/deploy-bastion.yaml --check
```

**Additional checks for second site:**

### Verify No Overlap with First Site

```bash
# Check IP ranges don't conflict
ansible-inventory -i inventory/production.yaml --list | jq '.[].ansible_host' | sort
ansible-inventory -i inventory/site2.yaml --list | jq '.[].ansible_host' | sort
```

**Expected:**
- Different IP ranges
- No duplicate IPs between sites

### Verify Different Site Name

```bash
ansible all -i inventory/site2.yaml -m debug -a "var=sentinelcam_site_name" --limit localhost
```

**Expected:**
- Different from first site (e.g., "remote" vs "chandler")

---

## 9. Group Membership Validation

### Check Infrastructure Group

```bash
ansible infrastructure -i inventory/production.yaml --list-hosts
```

**Expected:**
- Shows bastion host (e.g., chandler-gate)
- Only one host in infrastructure group

### Check SentinelCam Nodes Group

```bash
ansible sentinelcam_nodes -i inventory/production.yaml --list-hosts
```

**Expected:**
- Shows all SentinelCam nodes
- Does NOT include bastion (infrastructure) host

---

## 10. Syntax and Lint Checks

### YAML Syntax Validation

```bash
ansible-playbook --syntax-check -i inventory/production.yaml playbooks/deploy-bastion.yaml
```

**Expected:**
- "playbook: playbooks/deploy-bastion.yaml"
- No syntax errors

### Ansible Lint (Optional)

```bash
ansible-lint playbooks/deploy-bastion.yaml
```

**Review:**
- Any warnings or errors
- Fix critical issues before deploying

---

## Quick Validation Script

Save as `validate-site.sh`:

```bash
#!/bin/bash

INVENTORY=$1

if [ -z "$INVENTORY" ]; then
    echo "Usage: $0 <inventory-file>"
    exit 1
fi

echo "=== Validating Ansible Configuration for $INVENTORY ==="
echo

echo "1. Checking inventory structure..."
ansible-inventory -i "$INVENTORY" --graph

echo
echo "2. Checking site variables..."
ansible all -i "$INVENTORY" -m debug -a "var=sentinelcam_site_name" --limit localhost

echo
echo "3. Validating bastion hostname..."
ansible infrastructure -i "$INVENTORY" --list-hosts

echo
echo "4. Checking SentinelCam nodes..."
ansible sentinelcam_nodes -i "$INVENTORY" --list-hosts

echo
echo "5. Syntax check..."
ansible-playbook --syntax-check -i "$INVENTORY" playbooks/deploy-bastion.yaml

echo
echo "6. Dry run deployment..."
ansible-playbook -i "$INVENTORY" playbooks/deploy-bastion.yaml --check

echo
echo "=== Validation Complete ==="
```

Usage:
```bash
chmod +x validate-site.sh
./validate-site.sh inventory/production.yaml
./validate-site.sh inventory/site2.yaml
```

---

## Pre-Deployment Checklist Summary

Before running actual deployment:

- [ ] Inventory structure validated
- [ ] All hosts have `ansible_host` defined
- [ ] Site variables are set correctly
- [ ] `sentinelcam_bastion_hostname` matches infrastructure host
- [ ] Health check hosts exist in inventory
- [ ] DNS generation preview shows correct entries
- [ ] Hostname assertion test passes
- [ ] Variable precedence is understood
- [ ] Health check targets are reachable
- [ ] Dry run (`--check`) completes successfully
- [ ] No syntax errors
- [ ] Group membership is correct
- [ ] (If multi-site) No IP conflicts between sites
- [ ] (If multi-site) Different site names configured

---

## Common Issues and Fixes

### Issue: "Variable sentinelcam_bastion_hostname is undefined"

**Fix:**
```yaml
# Add to inventory vars section
all:
  vars:
    sentinelcam_bastion_hostname: "chandler-gate"
```

### Issue: "FAILED! => groups['datasinks'][0] is undefined"

**Fix:**
```yaml
# Ensure datasinks group exists with at least one host
datasinks:
  hosts:
    data1:
```

### Issue: "This playbook should only run on bastion host"

**Fix:**
- Ensure `sentinelcam_bastion_hostname` matches host in `infrastructure` group
- Check spelling and case sensitivity
- Verify inventory hostname vs node_name

### Issue: DNS entries not generated

**Fix:**
```yaml
# Check role defaults
bastion_dnsmasq:
  generate_from_inventory: true  # Must be true
  inventory_groups:
    - sentinelcam_nodes  # Must match your group name
```

---

## Post-Deployment Validation

After successful deployment:

```bash
# On bastion host
cat /etc/dnsmasq.d/sentinelcam.conf  # Check generated DNS entries
systemctl status dnsmasq             # Verify service running
nslookup data1 127.0.0.1             # Test DNS resolution
ping data1                           # Test connectivity
```

---

## Rollback Plan

If deployment fails:

1. **Restore from backup:**
   ```bash
   ansible bastion -i inventory/production.yaml -m shell -a \
     "ls -la /root/bastion-backups/"
   ```

2. **Restore specific config:**
   ```bash
   ansible bastion -i inventory/production.yaml -m shell -a \
     "cp /etc/dnsmasq.conf.backup.* /etc/dnsmasq.conf"
   ```

3. **Restart services:**
   ```bash
   ansible bastion -i inventory/production.yaml -m systemd -a \
     "name=dnsmasq state=restarted"
   ```

---

## Success Criteria

Deployment is successful when:

- ✅ All playbook tasks complete without errors
- ✅ DNS resolves all inventory hosts
- ✅ Health checks pass
- ✅ Services are running
- ✅ Internal network connectivity confirmed
- ✅ No changes to unrelated configuration
