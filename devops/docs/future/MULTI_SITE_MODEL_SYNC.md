# Multi-Site Model Synchronization

**Status**: Future Enhancement (Not Yet Implemented)

This document outlines the design for synchronizing the model registry across multiple SentinelCam sites when that capability is needed.

## Overview

Currently, the model registry exists only on the `primary_datasink` at a single site. When multiple sites are deployed (e.g., multiple properties, remote installations), a strategy is needed to keep model registries synchronized.

## Design Considerations

### Push vs Pull Architecture

**Push Model** (Control node initiates sync):
- Central control node pushes registry updates to remote sites
- Pros: Immediate updates, centralized control, audit trail
- Cons: Requires connectivity at sync time, central point of failure

**Pull Model** (Remote sites poll for updates):
- Remote sites periodically check for registry updates
- Pros: Works with intermittent connectivity, resilient
- Cons: Delayed updates, more complex state management

**Recommended**: Hybrid approach
- Push for immediate/critical updates
- Pull as fallback for sites with connectivity issues
- Sites can run autonomously with last-synced registry

### Sync Triggers

When should model sync occur?

1. **On-Demand**: Explicitly triggered after model deployment
2. **Scheduled**: Periodic sync (e.g., nightly) via cron/systemd timer
3. **Event-Driven**: Triggered by model_registry.yaml commit/update
4. **Manual**: Playbook execution by operator

**Recommended**: Combination of #1 (immediate) and #2 (safety net)

### Conflict Resolution

What happens when sites have diverged?

**Scenarios**:
- Site A: face_recognition v2025-12-08 (current)
- Site B: face_recognition v2025-11-20 (older)
- Site C: face_recognition v2025-12-09 (newer - experimental)

**Resolution Strategy**:
- **Authoritative Source**: Primary site registry is source of truth
- **Version Comparison**: Sync only if source version > target version
- **Preserve Local Overrides**: Host-level model_version_overrides remain intact
- **Conflict Handling**: 
  - Log conflicts where target > source
  - Require manual intervention for downgrades
  - Option: `--force-sync` flag to override

### Bandwidth Optimization

Model files can be large (hundreds of MB). Optimize transfers:

- **Incremental Sync**: Only sync new/changed versions
- **Rsync with `--checksum`**: Verify integrity, skip unchanged files
- **Compression**: Use `rsync -z` for slow links
- **Selective Sync**: Sync only models needed at target site
  - Site with only outposts: mobilenet_ssd only
  - Site with sentinels: all models
- **Delta Transfers**: Rsync's built-in delta algorithm

## Proposed Configuration Structure

### Site Registry Configuration

Add to `group_vars/all/site.yaml`:

```yaml
# Model registry synchronization
sentinelcam_model_sync:
  enabled: true
  mode: hybrid  # push, pull, hybrid
  
  # Primary (source) site
  primary_site:
    name: alpha_site
    registry_host: data1.alpha.local
    registry_path: /home/ops/sentinelcam/model_registry
  
  # Remote (target) sites
  remote_sites:
    beta_site:
      enabled: true
      registry_host: data1.beta.local
      registry_path: /home/ops/sentinelcam/model_registry
      models:  # Optional: filter which models to sync
        - mobilenet_ssd
        - face_detection
      sync_schedule: "0 2 * * *"  # Daily at 2 AM
      bandwidth_limit: 5000  # KB/s
    
    gamma_site:
      enabled: false  # Temporarily disabled
      registry_host: data1.gamma.local
      # ... other settings
```

### Sync Targets in Inventory

Alternatively, use inventory groups:

```ini
[model_sync_targets]
beta_site_datasink ansible_host=data1.beta.local
gamma_site_datasink ansible_host=data1.gamma.local
```

## Implementation Components

### 1. Sync Playbook

`playbooks/sync-models-multi-site.yaml`:
```yaml
---
- name: "Synchronize model registry to remote sites"
  hosts: primary_datasink
  gather_facts: no
  tasks:
    - name: "Sync registry to remote sites"
      ansible.posix.synchronize:
        src: "{{ sentinelcam_model_registry }}/"
        dest: "{{ sentinelcam_model_registry }}/"
        rsync_opts:
          - "--update"
          - "--checksum"
          - "--compress"
          - "--bwlimit={{ item.bandwidth_limit | default(0) }}"
      delegate_to: "{{ item.registry_host }}"
      loop: "{{ sentinelcam_model_sync.remote_sites | dict2items }}"
      when: item.value.enabled | default(true)
```

### 2. Systemd Timer (for scheduled pull/push)

`roles/datasink/templates/model_sync.timer.j2`:
```ini
[Unit]
Description=Model Registry Multi-Site Sync Timer

[Timer]
OnCalendar={{ site.sync_schedule | default('0 3 * * *') }}
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Sync Script with Conflict Detection

`scripts/sync_model_registry.sh`:
```bash
#!/bin/bash
# Multi-site model registry synchronization
# - Compare versions between sites
# - Log conflicts
# - Selective sync based on site needs
# - Bandwidth throttling
```

### 4. Webhook/Event Trigger (optional)

For event-driven sync:
- Git post-commit hook on model_registry.yaml changes
- Ansible callback plugin to trigger sync after model deployment
- REST API endpoint on datasinks to accept sync requests

## Network Topologies

### Scenario A: Hub and Spoke
```
Primary Site (Alpha)
       |
   ----+----
   |       |
Beta     Gamma
```
- Primary pushes to all remotes
- Remotes never sync to each other

### Scenario B: Mesh (Complex)
```
Alpha <-> Beta
  ^       ^
   \     /
    Gamma
```
- Any site can be source
- Requires conflict resolution
- More complex, not recommended initially

**Recommended**: Scenario A (Hub and Spoke)

## Security Considerations

### Authentication
- SSH key-based authentication (no passwords)
- Dedicated sync user with limited permissions
- Keys distributed via Ansible (existing pattern)

### Authorization
- Sync user can only write to registry directory
- Read-only access to model_registry.yaml
- No sudo/privilege escalation

### Integrity
- SHA256 checksums in manifest.yaml
- Verify checksums post-sync
- Alert on checksum mismatches

### Network
- VPN or SSH tunnels for site-to-site sync
- Optional: WireGuard mesh for always-on connectivity

## Monitoring and Logging

### What to Log
- Sync initiation (timestamp, source, target)
- Models synced (name, version, size)
- Duration and bandwidth used
- Conflicts detected
- Errors/failures

### Metrics
- Last successful sync timestamp
- Sync duration trend
- Bandwidth consumed
- Model staleness (version age difference)

### Alerting (optional)
- Sync failures after N retries
- Registry version drift exceeds threshold
- Disk space low on registry

## Migration Path

When multi-site is needed:

1. **Phase 1**: Document requirements
   - How many sites?
   - Connectivity between sites?
   - Which models per site?
   - Sync frequency needs?

2. **Phase 2**: Configure inventory
   - Define model_sync_targets group
   - Add site-specific variables
   - Test connectivity

3. **Phase 3**: Implement sync playbook
   - Start with manual execution
   - Add validation checks
   - Test rollback scenarios

4. **Phase 4**: Automate
   - Deploy systemd timers
   - Configure monitoring
   - Document procedures

5. **Phase 5**: Optimize
   - Tune rsync options
   - Implement bandwidth limits
   - Add conflict detection

## Future Enhancements

- **Registry Mirroring**: Full replica at each site
- **Model Caching**: CDN-like distribution
- **Distributed Versioning**: Git-like model tracking
- **Automated Testing**: Validate models post-sync
- **Rollback Coordination**: Sync rollback commands across sites

## Decision Log

When multi-site sync is implemented, document:
- Which architecture chosen (push/pull/hybrid)
- Sync frequency decision
- Bandwidth constraints
- Conflict resolution rules
- Testing results

## References

- Rsync documentation: https://rsync.samba.org/
- Ansible synchronize module: https://docs.ansible.com/ansible/latest/collections/ansible/posix/synchronize_module.html
- Similar patterns:
  - Outpost registry (single-site, host-level filtering)
  - Code deployment (rsync-based, single source)

---

**Document Status**: Planning/Design  
**Implementation Required**: No (pending requirements)  
**Last Updated**: 2025-12-08  
**Next Review**: When multi-site deployment is needed
