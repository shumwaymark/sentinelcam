# Thermal Monitoring Integration Guide

Complete guide for integrating accelerator thermal monitoring into SentinelCam's inference pipelines. This document provides configuration schemas, code integration examples, deployment procedures, and troubleshooting guidance.

## Overview

The thermal monitoring system detects and responds to accelerator performance degradation caused by thermal throttling. It uses a lightweight performance-based approach suitable for continuous operation with negligible overhead (<0.1%).

**Key Features:**
- Performance-based thermal detection (works without temperature sensors)
- Automatic cooldown injection when degradation detected
- Device-specific optimizations (Coral USB, DepthAI/OAK, NCS2, CPU)
- Host-level configuration with optional task overrides
- ZeroMQ metrics integration with existing logging infrastructure

**When to Use:**
- Batch processing workloads (sentinel) with continuous inference
- Real-time edge processing (outpost/spyglass) during extended events
- Multi-accelerator configurations (dual-Coral setups)
- Any workload with sustained high TPU/VPU utilization

## Architecture

### Module: `sentinelcam/monitoring.py`

Self-contained monitoring module with no dependencies on sentinel or imagenode. Uses only stdlib + numpy.

**Classes:**
- `AcceleratorPerformanceMonitor` - Base class with timing and degradation logic
- `CoralMonitor` - Google Coral USB (performance-based only)
- `DepthAIMonitor` - DepthAI/OAK cameras (performance + temperature)
- `GenericMonitor` - CPU, NCS2, or custom accelerators
- `create_monitor()` - Factory function for creating monitors

### Integration Points

**Sentinel** (`sentinel/sentinel/sentinel.py`):
- Line ~383: TaskFactory instantiation - pass thermal config
- Lines ~410-420: Frame loop in `taskHost()` - wrap `task.pipeline()` with timing

**Spyglass** (`imagenode/imagenode/sentinelcam/spyglass.py`):
- Lens detection classes - wrap inference calls with timing
- Concurrent pipeline - check degradation between frames

## Configuration

### Ansible Host Variables

Thermal monitoring is configured at the **host level** in ansible `host_vars`, not in task configurations. This aligns with hardware topology (one config per physical device) and avoids duplication across tasks using the same accelerator.

**Location:** `ansible/host_vars/<hostname>/thermal_monitoring.yaml`

**Schema:**
```yaml
# Host-level thermal monitoring configuration
thermal_monitoring:
  enabled: true
  
  # Accelerator-specific configurations
  accelerators:
    coral:
      window_size: 25              # Inference times to track (default: 25)
      check_interval: 25           # Check every N frames (default: 25)
      degradation_threshold: 1.5   # Trigger at Nx baseline (default: 1.5)
      cooldown_duration: 30        # Sleep duration in seconds (default: 30)
    
    depthai:
      window_size: 30
      check_interval: 25
      degradation_threshold: 1.3   # Lower for devices with temp sensors
      cooldown_duration: 25
    
    cpu:
      window_size: 50
      check_interval: 50
      degradation_threshold: 2.0   # Higher threshold for CPU
      cooldown_duration: 20
```

### Parameter Selection Rationale

The default monitoring parameters are **engineering estimates** based on observed Coral USB thermal behavior in production batch processing workloads (sentinel log analysis, January 2026). These values should be considered **v1.0 starting points** requiring production validation and tuning.

#### Design Philosophy: Per-Engine Monitoring

Thermal monitoring operates at the **engine level**, not the task level:

- **Thermal state is hardware property**: Heat accumulation occurs in the physical accelerator (Coral USB), not in software tasks
- **Task swaps don't cool devices**: When primary engine switches from MobileNetSSD → GetFaces, the Coral remains thermally loaded
- **Rolling averages persist across tasks**: Baseline and timing windows continue through task transitions
- **Engine isolation**: Each task engine maintains its own monitor instance (though only primary has Coral access)

**Rationale**: A task running at 35 fps followed by another at 28 fps could indicate either (a) second task is heavier, or (b) Coral is thermally throttling. Rolling averages across tasks allow detection of gradual thermal degradation independent of workload shifts.

#### Observed Task Performance Characteristics

Production logging reveals clear performance patterns that inform parameter selection:

**Startup Overhead Dominates Small Events:**
- Small events (< 50 frames): Average 18.3 fps
- Medium events (50-199 frames): Average 27.0 fps  
- Large events (200+ frames): Average 28.6 fps

The 10 fps gap between small and large events reflects fixed overhead (event loading, model initialization, result transmission) amortized across frames. This variance is **normal workload behavior**, not thermal throttling.

**Model Execution Speed Varies:**
- MobileNetSSD_allFrames: 20-32 fps typical (processes every frame)
- GetFaces: 35-37 fps typical (processes only person-containing frames, chained from MobileNetSSD)
- FaceRecon: CPU-bound, no Coral usage

GetFaces runs consistently faster because it's chained from object detection - only frames with detected persons are processed, resulting in fewer inference calls and lighter workload.

**Thermal Degradation Pattern:**
- Fresh start: 29-30 fps
- After 3 hours continuous: 26-27 fps (10% decline)
- Overnight without cooling: 7-10 fps → 0 fps (catastrophic throttling)

#### Parameter Values and Justification

**`window_size: 25` frames**
- **Basis**: At 30 fps, 25 frames ≈ 0.75-1 second of data
- **Trade-off**: Responsive to thermal changes without excessive noise from per-frame variance
- **Signal processing pattern**: Sufficient samples for stable statistics
- **Alternative**: Could increase to 50 for more stable baseline in highly variable workloads

**`check_interval: 25` frames**
- **Basis**: Check every time rolling window fills (aligned with window_size)
- **Overhead**: ~5µs per check = negligible (<0.001%)
- **Responsiveness**: Detect degradation within ~1 second of onset
- **Alternative**: Increase to 50-100 to reduce check frequency if false positives occur

**`degradation_threshold: 1.5x` baseline**
- **Basis**: Observed Coral throttling shows 30ms → 45-50ms inference time (1.5-1.7x slower)
- **Above normal variance**: 10 fps difference between small/large events (1.3x) is normal; 1.5x catches actual thermal issues
- **Conservative**: Avoids false positives from workload complexity changes (MobileNetSSD → GetFaces swap)
- **Production validation needed**: Monitor for false positives (trigger when not throttling) or false negatives (miss actual throttling)

**`cooldown_duration: 30` seconds**
- **Basis**: Small USB devices (2.5W) typically dissipate heat in 30-60 seconds with passive cooling
- **Empirical**: Sentinel restart cleared overnight throttling, confirming recovery is possible
- **Trade-off**: Long enough to cool effectively, short enough to minimize throughput impact
- **With Vornado fan**: 30s should be sufficient; may need 45-60s without active cooling

**DepthAI `degradation_threshold: 1.3x`** (tighter)
- **Basis**: Temperature sensor provides hardware confirmation of thermal state
- **Cross-validation**: Performance degradation can be verified against actual temperature readings
- **Earlier intervention**: Catch throttling before it becomes severe

**`temperature_threshold: 80°C`** (DepthAI only)
- **Industry standard**: Most edge AI chips specify 85°C max junction temperature
- **Safety margin**: 5°C headroom before hitting absolute limits
- **Failsafe**: Hardware-level protection independent of performance monitoring

#### Expected Monitoring Overhead

Validated via `monitoring_demo.py` with 10,000 frame simulation:

| Operation | Time | Impact |
|-----------|------|--------|
| `record_inference()` | <1µs | <0.001% |
| `should_check_now()` | <1µs | <0.001% |
| `is_degraded()` (at intervals) | 3-5µs | ~0.015% |
| **Total per frame** | **~5µs** | **~0.015%** |

At 30 fps with 30ms inference time, monitoring adds 5µs = 0.017% overhead. This is negligible.

**Cooldown overhead** (when thermal events occur):
- 2-3 cooldowns per hour × 30s each = 90s/hour = 2.5% throughput reduction during thermal stress
- Without monitoring: 10% performance sag over 3 hours = 5% average throughput loss
- **Net benefit**: ~2-3% throughput improvement from maintaining peak performance

#### Production Tuning Guidance

After deployment, monitor these metrics and adjust parameters:

**If seeing false positive cooldowns (not actually throttling):**
- Increase `degradation_threshold` (1.5 → 1.8 or 2.0)
- Increase `window_size` (25 → 50) for more stable baseline
- Increase `check_interval` (25 → 50 or 100) to reduce check frequency
- Verify timing wrapper only includes inference, not I/O or preprocessing

**If missing thermal throttling (degradation continues unchecked):**
- Decrease `degradation_threshold` (1.5 → 1.3)
- Decrease `window_size` (25 → 15) for faster response
- Decrease `check_interval` (25 → 10) for more frequent checks
- Add debug logging to verify timing records are captured

**If cooldowns insufficient (performance doesn't recover):**
- Increase `cooldown_duration` (30 → 45 or 60 seconds)
- Check physical cooling: Add fan, improve airflow, verify device not enclosed
- Consider hardware limitations: May need powered USB hub for better power delivery

**If cooldowns too frequent (excessive throughput impact):**
- Optimize cooling first: Add GPIO fan, improve ambient temperature
- Balance cooldown parameters: Increase duration, decrease frequency
- Consider workload scheduling: Add breaks between heavy batch jobs

#### Design Decisions: Baseline Persistence

**Rolling Average Across Task Swaps** (Recommended):

Monitor maintains rolling window through task transitions. When primary engine switches MobileNetSSD → GetFaces → back to MobileNetSSD, the timing window and baseline persist.

**Advantages:**
- Detects gradual thermal accumulation independent of task changes
- Hardware thermal state doesn't reset when tasks swap
- Prevents false negatives: Won't miss throttling that spans multiple tasks
- Simpler implementation: No state reset logic needed

**Disadvantages:**
- Baseline may be biased if first task is consistently faster/slower
- Task-specific performance characteristics averaged together

**Per-Task Baseline Reset** (Alternative):

Clear timing window and recalculate baseline on each new task.

**Advantages:**
- Baseline reflects specific task characteristics (MobileNetSSD vs GetFaces speed difference)
- More accurate per-task degradation detection

**Disadvantages:**
- Can't detect gradual thermal accumulation across task boundaries
- False positives during task transitions (old baseline doesn't match new task)
- Requires 25+ frames of new task before baseline established
- More complex: Need task change detection and state reset logic

**Recommendation**: Use rolling average with sufficient `degradation_threshold` (1.5x) to accommodate normal task performance variance. The 1.5x threshold is large enough to handle GetFaces being 20-30% faster than MobileNetSSD while still catching thermal throttling (1.7x+ slower). This approach aligns with the physical reality: **thermal state is a property of the hardware, not the software task**.

#### Validation Metrics

After deployment, collect these metrics to validate parameter selection:

1. **Cooldown frequency**: Target 2-3 per hour during sustained batch processing
2. **False positive rate**: <5% of cooldowns should be during normal operation
3. **Recovery effectiveness**: Performance should return to within 10% of baseline after cooldown
4. **Throughput impact**: Total cooldown overhead should be <3% of processing time
5. **Variance reduction**: FPS range should tighten from 9.5-35 to 20-33 after deployment

These targets are based on observed thermal patterns in sentinel logs (January 2026) and may need adjustment based on different workload profiles or hardware configurations.

### Example Configurations

#### Sentinel (Server-Side Batch Processing)

**File:** `ansible/host_vars/clovis/thermal_monitoring.yaml`

Sentinel has 3 task engines but only 1 Coral USB shared across all engines. Configure once at host level.

```yaml
# clovis.thermal_monitoring.yaml - Sentinel configuration
thermal_monitoring:
  enabled: true
  
  accelerators:
    coral:
      window_size: 25
      check_interval: 25
      degradation_threshold: 1.5
      cooldown_duration: 30
      
  # Optional: logging detail level
  log_level: INFO  # DEBUG for detailed per-frame metrics
```

**Rationale:** 
- 25-frame window = ~0.75s of inference at 30 fps (fast response)
- 1.5x threshold = trigger at 45ms+ when baseline is 30ms
- 30s cooldown = allows sustained cooling without excessive interruption

#### Outpost (Edge Node with Multiple Accelerators)

**File:** `ansible/host_vars/deepend/thermal_monitoring.yaml`

Outpost may have multiple accelerator types (Coral + DepthAI). Configure each separately.

```yaml
# deepend.thermal_monitoring.yaml - Outpost with dual accelerators
thermal_monitoring:
  enabled: true
  
  accelerators:
    coral:
      window_size: 25
      check_interval: 25
      degradation_threshold: 1.5
      cooldown_duration: 30
    
    depthai:
      window_size: 30
      check_interval: 25
      degradation_threshold: 1.3   # Tighter threshold with temp sensors
      cooldown_duration: 25
      temperature_threshold: 80.0  # Celsius (DepthAI-specific)
```

**Rationale:**
- DepthAI has temperature sensors → use tighter degradation threshold
- Different cooldown durations reflect thermal characteristics
- Temperature threshold provides hardware-level failsafe

#### Future Multi-Coral Sentinel

**File:** `ansible/host_vars/clovis/thermal_monitoring.yaml` (updated)

When dual-Coral configuration deployed, single `coral` config applies to both devices.

```yaml
# Multi-Coral configuration (future)
thermal_monitoring:
  enabled: true
  
  accelerators:
    coral:
      window_size: 25
      check_interval: 25
      degradation_threshold: 1.5
      cooldown_duration: 30
      
  # Multi-device strategy
  stagger_cooldowns: true  # Prevent simultaneous cooldowns
  cooldown_stagger_delay: 15  # Seconds between device cooldowns
```

**Rationale:**
- Staggered cooldowns maintain partial processing capacity
- One Coral continues while other cools
- Reduces overall throughput impact

### Task-Level Overrides (Optional)

For tasks with special requirements, override host config in `sentinel.yaml` task definitions.

**File:** `sentinel.yaml`

```yaml
task_list:
  gamma4:
    class: Detection
    alias: FaceDetection
    config:
      model: gamma4_model
      
      # Optional task-specific thermal override
      thermal_monitoring:
        check_interval: 50      # Less frequent checks
        cooldown_duration: 45   # Longer cooldown for heavy models
```

**Configuration Precedence:**
1. Task-specific config (highest priority)
2. Host-level accelerator config
3. Hardcoded defaults in `monitoring.py`

## Integration Code

### Sentinel Integration

**File:** `sentinel/sentinel/sentinel.py`

#### Step 1: Import Module

```python
# Add to imports at top of file
from sentinelcam.monitoring import create_monitor
```

#### Step 2: Create Monitor in taskHost()

```python
def taskHost(self, engineName, pump, taskCFG, accelerator, taskQ, _ringbuff):
    try:
        # ... existing setup code ...
        
        # Initialize thermal monitor for this engine
        thermal_monitor = None
        if CFG.get('thermal_monitoring', {}).get('enabled', False):
            thermal_config = CFG['thermal_monitoring']['accelerators'].get(accelerator, {})
            thermal_monitor = create_monitor(accelerator, config=thermal_config)
            logging.info(f"{engineName}: Thermal monitoring enabled for {accelerator}")
        
        # ... rest of taskHost setup ...
```

#### Step 3: Wrap Task Execution

Find the frame loop around line 410:

```python
# BEFORE: Tight frame loop with no delays
while bucket != JobManager.ReadEOF:
    if task.pipeline(self.ringbuff[bucket]):
        bucket = ringNext()
    else:
        bucket = JobManager.ReadEOF
```

**Replace with:**

```python
# AFTER: Frame loop with thermal monitoring
while bucket != JobManager.ReadEOF:
    # Time the inference
    inference_start = time.time()
    pipeline_result = task.pipeline(self.ringbuff[bucket])
    
    # Record timing in thermal monitor
    if thermal_monitor:
        thermal_monitor.record_inference(inference_start, time.time())
        
        # Check for degradation at intervals
        if thermal_monitor.should_check_now() and thermal_monitor.is_degraded():
            metrics = thermal_monitor.trigger_cooldown()
            msg = (TaskEngine.TaskWARNING, 
                   f"Thermal cooldown #{metrics['cooldown_count']}: "
                   f"{metrics['baseline_ms']}ms → {metrics['current_avg_ms']}ms")
            publisher.send(msgpack.packb(msg))
    
    if pipeline_result:
        bucket = ringNext()
    else:
        bucket = JobManager.ReadEOF
```

#### Step 4: Task Metrics (Optional Enhancement)

For detailed per-task thermal metrics, add to finalize():

```python
def finalize(self) -> bool:
    # ... existing finalization ...
    
    # Log thermal metrics if monitoring enabled
    if hasattr(self, 'thermal_monitor') and self.thermal_monitor:
        metrics = self.thermal_monitor.get_metrics()
        logging.info(f"Task thermal summary: {json.dumps(metrics)}")
    
    return True
```

### Spyglass Integration (Outpost)

**File:** `imagenode/imagenode/sentinelcam/spyglass.py`

#### Step 1: Import and Initialize

```python
from sentinelcam.monitoring import create_monitor

class SpyGlass:
    def __init__(self, config):
        # ... existing init ...
        
        # Initialize thermal monitor
        self.thermal_monitor = None
        if config.get('thermal_monitoring', {}).get('enabled', False):
            accel_type = config.get('accelerator', 'cpu')
            thermal_config = config['thermal_monitoring']['accelerators'].get(accel_type, {})
            self.thermal_monitor = create_monitor(accel_type, config=thermal_config)
```

#### Step 2: Wrap Lens Detection

```python
class MobileNetSSD_Lens(Lens):
    def detect(self, frame):
        # Time the inference
        if self.spyglass.thermal_monitor:
            inference_start = time.time()
        
        # Existing detection logic
        results = self.detector.infer(frame)
        
        # Record timing
        if self.spyglass.thermal_monitor:
            self.spyglass.thermal_monitor.record_inference(inference_start, time.time())
            
            # Check degradation
            if (self.spyglass.thermal_monitor.should_check_now() and 
                self.spyglass.thermal_monitor.is_degraded()):
                metrics = self.spyglass.thermal_monitor.trigger_cooldown()
                self.log.warning(f"Thermal cooldown: {metrics}")
        
        return results
```

## Deployment

### Phase 1: Add Host Variables (No Code Changes)

1. Create thermal monitoring configs in ansible host_vars:
   ```bash
   cd ansible/host_vars/<hostname>
   vi thermal_monitoring.yaml
   ```

2. Add configuration following examples above

3. Commit to version control:
   ```bash
   git add ansible/host_vars/*/thermal_monitoring.yaml
   git commit -m "Add thermal monitoring host configurations"
   ```

**Status:** Ready for integration, zero production impact

### Phase 2: Integrate Monitoring (Code Changes)

**Timing:** After gamma4 Phases 4-5 complete (late January 2026)

1. Apply code changes to sentinel and spyglass (see integration code above)

2. Test on development node:
   ```bash
   # Run demo to validate behavior
   cd sentinelcam
   python -m sentinelcam.monitoring_demo --degradation
   
   # Run unit tests
   python -m pytest tests/test_monitoring.py -v
   ```

3. Deploy to test sentinel (clovis):
   ```bash
   ansible-playbook deploy_sentinel.yaml --limit clovis --tags code
   ```

4. Monitor logs for thermal events:
   ```bash
   ssh ops@clovis "tail -f /var/log/sentinel/sentinel.log" | grep -i thermal
   ```

5. Validate behavior:
   - Submit batch job processing several hours of data
   - Watch for thermal warnings in logs
   - Confirm cooldowns trigger and performance recovers
   - Verify no false positives during normal operation

### Phase 3: Production Rollout

1. Deploy to remaining sentinels and outposts:
   ```bash
   ansible-playbook deploy_all.yaml --tags code
   ```

2. Update monitoring queries in camwatcher to track thermal events

3. Document thermal patterns for different workload types

## Rollback Procedure

If thermal monitoring causes issues after integration:

### Quick Disable (No Code Changes)

**Option 1:** Disable via host_vars

```yaml
# Set enabled: false in thermal_monitoring.yaml
thermal_monitoring:
  enabled: false
```

Restart services:
```bash
ansible-playbook restart_services.yaml --limit <hostname>
```

**Option 2:** Remove config entirely

```bash
# Remove thermal_monitoring.yaml files
ansible-playbook deploy_all.yaml --skip-tags thermal
```

### Full Rollback (Remove Code Integration)

1. Revert code changes:
   ```bash
   git revert <commit_hash_of_integration>
   ```

2. Redeploy:
   ```bash
   ansible-playbook deploy_all.yaml --tags code
   ```

## Monitoring & Observability

### Log Format

Thermal events publish to ZeroMQ logging infrastructure using existing patterns:

**Cooldown Triggered:**
```json
{
  "flag": "THERMAL_COOLDOWN",
  "engine": "engine1",
  "jobid": "abc123...",
  "accelerator": "coral",
  "cooldown_number": 1,
  "baseline_ms": 30.5,
  "current_avg_ms": 52.3,
  "degradation_ratio": 1.71,
  "frames_processed": 1250,
  "timestamp": "2026-01-15T14:23:41.123456"
}
```

**Periodic Status (Debug Level):**
```json
{
  "flag": "THERMAL_STATUS",
  "accelerator": "coral",
  "frame_count": 2500,
  "baseline_ms": 30.5,
  "current_avg_ms": 31.2,
  "degraded": false,
  "cooldown_count": 2,
  "frames_since_cooldown": 1200
}
```

### Camwatcher Queries

**Cooldown Frequency:**
```python
# Count cooldowns per day
SELECT date(timestamp) as date, 
       COUNT(*) as cooldown_count
FROM logs 
WHERE flag = 'THERMAL_COOLDOWN'
GROUP BY date(timestamp)
ORDER BY date DESC;
```

**Degradation Patterns:**
```python
# Average degradation ratio when cooldowns triggered
SELECT accelerator,
       AVG(current_avg_ms / baseline_ms) as avg_degradation,
       MAX(current_avg_ms / baseline_ms) as max_degradation
FROM logs
WHERE flag = 'THERMAL_COOLDOWN'
GROUP BY accelerator;
```

**Task Impact:**
```python
# Cooldowns per task type
SELECT task, COUNT(*) as cooldowns
FROM logs
WHERE flag = 'THERMAL_COOLDOWN'
GROUP BY task
ORDER BY cooldowns DESC;
```

### Watchtower Dashboard

Add thermal monitoring panel:

```python
# Thermal status by host
thermal_status = {
    'clovis': get_thermal_metrics('clovis'),  # sentinel
    'deepend': get_thermal_metrics('deepend'),  # outpost
    'alpha5': get_thermal_metrics('alpha5')  # outpost
}

# Alert if cooldown rate exceeds threshold
for host, metrics in thermal_status.items():
    if metrics['cooldowns_per_hour'] > 5:
        alert(f"{host}: High thermal activity ({metrics['cooldowns_per_hour']}/hr)")
```

## Troubleshooting

### Issue: False Positive Cooldowns

**Symptoms:** Cooldowns triggered during normal operation with no actual throttling

**Diagnosis:**
1. Check baseline establishment: `"baseline_ms": null` indicates not enough samples
2. Review degradation threshold: May be too aggressive for workload variability
3. Verify inference timing: Ensure timing wraps only inference, not I/O or preprocessing

**Solutions:**
- Increase `window_size` for more stable baseline (25 → 50)
- Raise `degradation_threshold` (1.5 → 1.8)
- Increase `check_interval` to reduce check frequency
- Add timing guards to exclude non-inference overhead

### Issue: Missed Throttling Events

**Symptoms:** Performance degradation continues without cooldown triggering

**Diagnosis:**
1. Check `thermal_monitoring.enabled` in host_vars
2. Verify monitor created: Look for "Thermal monitoring enabled" in logs
3. Review threshold: May be too loose for actual degradation pattern
4. Check frame count: Need `window_size` frames before baseline set

**Solutions:**
- Lower `degradation_threshold` (1.5 → 1.3)
- Decrease `window_size` for faster response (25 → 15)
- Decrease `check_interval` for more frequent checks (25 → 10)
- Add debug logging to verify timing records

### Issue: Excessive Cooldown Duration

**Symptoms:** Long pauses in processing, low overall throughput

**Diagnosis:**
1. Check `cooldown_duration` setting (should be 20-45s)
2. Review cooldown frequency: Multiple cooldowns may indicate insufficient duration
3. Check hardware: Inadequate cooling (no heatsink, poor airflow)

**Solutions:**
- Optimize first: Add active cooling (fan, heatsink)
- Balance cooldown: Increase duration but reduce frequency
- Stagger cooldowns in multi-device setups
- Consider workload changes: Add breaks between batch jobs

### Issue: Monitor Overhead Impact

**Symptoms:** Measurable slowdown after thermal monitoring enabled

**Diagnosis:**
1. Profile monitoring overhead: Should be <0.1%
2. Check timing method: `time.time()` is lightweight
3. Review check frequency: Excessive checks can add up

**Solutions:**
- Increase `check_interval` (25 → 100) to reduce check frequency
- Verify timing code placement (only wrap inference, not I/O)
- Consider disabling debug logging if enabled
- Report issue - overhead should be negligible

## Performance Characteristics

### Overhead Measurements

Validated via `monitoring_demo.py`:

| Operation | Time per Frame | Percentage |
|-----------|---------------|------------|
| Inference (baseline) | 30-35ms | 100% |
| `record_inference()` | <1µs | <0.001% |
| `should_check_now()` | <1µs | <0.001% |
| `is_degraded()` | 3-5µs | 0.015% |
| **Total monitoring** | **~5µs** | **~0.015%** |

**Cooldown overhead:** 30s every 30-45 minutes = <2% throughput impact during thermal events

### Thermal Characteristics by Device

| Device | Throttle Onset | Recovery Time | Recommended Cooldown |
|--------|---------------|---------------|---------------------|
| Coral USB | 15-30 min | 30-60s | 30-45s |
| NCS2 | 20-40 min | 45-90s | 60s |
| DepthAI/OAK | 45-90 min | 20-40s | 25-30s |
| CPU | Varies | Varies | 20s |

**Note:** Times vary with ambient temperature, enclosure, airflow, and workload intensity.

## Future Enhancements

### Adaptive Cooldown Duration

Currently cooldown is fixed. Could adapt based on recovery rate:

```python
# Measure performance after cooldown
post_cooldown_perf = monitor.get_current_performance()
if post_cooldown_perf > baseline * 1.2:
    # Didn't fully recover, increase cooldown next time
    monitor.cooldown_duration += 5
```

### Multi-Device Coordination

For dual-Coral configurations, coordinate cooldowns to maintain capacity:

```python
class CoordinatedMonitor:
    def trigger_cooldown(self):
        # Check if another device is cooling
        if not coordinator.any_cooling():
            super().trigger_cooldown()
            coordinator.register_cooling(self)
        else:
            # Defer cooldown until other device recovers
            coordinator.queue_cooldown(self)
```

### Temperature-Based Prediction

For devices with temperature sensors, predict throttling before it occurs:

```python
def predict_throttle(self) -> bool:
    temp = self._get_device_temperature()
    temp_trend = self._calculate_temp_trend()
    
    # Trigger preemptive cooldown if trending toward threshold
    if temp > 70 and temp_trend > 0.5:  # Rising quickly
        return True
```

### Integration with Task Scheduler

Allow scheduler to defer non-urgent tasks during thermal stress:

```python
# In JobManager
if any(engine.thermal_monitor.is_degraded() for engine in engines):
    # Prioritize urgent tasks, defer batch jobs
    pending_tasks.sort(key=lambda t: t.priority if t.urgent else 999)
```

## References

- [Coral USB Performance Docs](https://coral.ai/docs/accelerator/get-started/)
- [DepthAI Temperature Management](https://docs.luxonis.com/projects/api/en/latest/references/python/#depthai.Device.getChipTemperature)
- [sentinelcam/monitoring.py](../sentinelcam/monitoring.py) - Module source
- [sentinelcam/monitoring_demo.py](../sentinelcam/monitoring_demo.py) - Demo script
- [tests/test_monitoring.py](../tests/test_monitoring.py) - Unit tests
- [docs/GAMMA4_WORKFLOW.md](GAMMA4_WORKFLOW.md) - Current priority workflow

## Support

For issues or questions:
1. Check troubleshooting section above
2. Review logs for thermal events and patterns
3. Run monitoring_demo.py to validate behavior
4. Consult ansible host_vars for current configuration
5. Contact: mark.shumway@swanriver.dev
