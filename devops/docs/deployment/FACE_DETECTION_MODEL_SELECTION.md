# Face Detection Model Selection

The sentinel role supports per-host model selection for face detection. Which model runs on
a given sentinel node is controlled entirely through Ansible variables — no task files need
to be edited. The `GetFaces.yaml.j2` template reads the host's configuration and generates
the correct model paths and confidence thresholds at deploy time.

## How It Works

Three variables control the selection. Set these in `host_vars/<hostname>.yaml` or accept the
defaults from `roles/sentinel/defaults/main.yaml`:

```yaml
sentinel_accelerator_type: coral     # coral, cpu, or ncs2
sentinel_face_detection_model: blazeface  # blazeface or ssd_mobilenet (coral only)
sentinel_blazeface_variant: full     # full (0-5m) or short (0-2m)
```

The template `roles/sentinel/templates/tasks/GetFaces.yaml.j2` branches on these values:

- **Coral + BlazeFace**: Uses the MediaPipe BlazeFace EdgeTPU model (full or short range)
- **Coral + SSD MobileNet**: Uses `ssd_mobilenet_v2_face_quant_postprocess_edgetpu.tflite`
- **CPU or NCS2**: Uses OpenCV DNN with `res10_300x300_ssd_iter_140000.caffemodel`

Confidence thresholds are model-specific and also overridable per host:

```yaml
face_detection_confidence:
  cpu: 0.5
  ncs2: 0.5
  ssd_mobilenet: 0.5
  blazeface: 0.5
```

Model file paths are constructed automatically from the model registry versions defined in
`group_vars/all/model_registry.yaml`, with optional per-host version pinning through
`model_version_overrides`.

## Switching Models

To change which model a sentinel uses:

1. Edit (or create) `inventory/host_vars/<hostname>.yaml`:
   ```yaml
   sentinel_face_detection_model: blazeface
   ```

2. If switching to BlazeFace, ensure the models are deployed:
   ```bash
   ansible-playbook playbooks/deploy-models.yaml --tags blazeface
   ```

3. Deploy the configuration and restart:
   ```bash
   ansible-playbook playbooks/deploy-sentinel.yaml --tags config --limit <hostname>
   ansible <hostname> -m systemd -a "name=sentinel state=restarted" -b
   ```

4. Verify the generated task file:
   ```bash
   ssh ops@<hostname> cat /home/ops/sentinel/tasks/GetFaces.yaml
   ```

To roll back, change `sentinel_face_detection_model` back and redeploy. To fall back to CPU
entirely, set `sentinel_accelerator_type: cpu`.

## Key Files

| File | Purpose |
|------|---------|
| `roles/sentinel/defaults/main.yaml` | Default model selection, paths, confidence thresholds |
| `roles/sentinel/templates/tasks/GetFaces.yaml.j2` | Template that generates face detection config |
| `inventory/host_vars/sentinel_example.yaml.example` | Annotated example with all options |
| `playbooks/deploy-models.yaml` | Deploys model files from registry to sentinel nodes |
| `group_vars/all/model_registry.yaml` | Model version definitions |
