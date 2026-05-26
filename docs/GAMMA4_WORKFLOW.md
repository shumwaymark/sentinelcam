# Facial Recognition Model Gamma4 - Assessment and Training Workflow

## Current Status

**Date:** 2026-01-01  
**Model:** gamma3 (deployed 2025-02-25)  
**Data Gap:** 24 months (Jan 2024 - Dec 2025) unprocessed  
**Next Version:** gamma4 with model registry integration

## Infrastructure Updates Complete

### ✅ Step 1-4: Foundation Built

1. **FaceSweep Submission Tool** - [twister/face_sweep_dates.py](../twister/face_sweep_dates.py)
   - Submit tasks for sample dates or custom ranges
   - 28 sample dates across 2024-2025 (3-4 per quarter)
   - Usage: `python face_sweep_dates.py --mode sample --dry-run`

2. **Facelist Curation Workflow** - [twister/sentinelcam/face_curation.py](../twister/sentinelcam/face_curation.py)
   - Bidirectional sync with sentinel
   - State persistence and checkpointing
   - Usage in notebook: `from sentinelcam.face_curation import FaceCurationWorkflow`

3. **Deepend Role Modernized** - [devops/ansible/roles/deepthink/](../devops/ansible/roles/deepthink/)
   - Model registry versioning (YYYY-MM-DD format)
   - Single `model_version` parameter
   - Updated training script: [train_face_model.sh.j2](../devops/ansible/roles/deepthink/templates/train_face_model.sh.j2)
   - Uses existing upload_and_deploy for registry integration

4. **Training Notebook Enhanced** - [twister/facemodel_build_beta.ipynb](../twister/facemodel_build_beta.ipynb)
   - Single parameter: `model_version` (YYYY-MM-DD)
   - Class balancing: `class_weight='balanced'`
   - SMOTE oversampling for classes <15 examples
   - Per-class metrics and confusion matrix
   - Enhanced diagnostics

## Next Steps

### Phase 1: Sample Health Assessment (Week 1-2)

**Goal:** Understand gamma3 performance on 2024-2025 data before full processing

```bash
# On twister workstation
cd ~/SRS/sentinelcam/twister

# 1. Submit FaceSweep for sample dates (dry run first)
python face_sweep_dates.py --mode sample --dry-run

# 2. After reviewing, submit for real
python face_sweep_dates.py --mode sample --sentinel tcp://sentinel:5566

# 3. Wait for tasks to complete (~1-2 days, check sentinel logs)
# 4. Download facelist for review
python -m sentinelcam.face_curation download --file facelist_sample.csv

# 5. Open assessment notebook
jupyter notebook facelist_gamma4_assessment.ipynb
```

**Assessment Notebook Tasks:**
- Load sample candidates (status=0)
- Analyze gamma3 prediction distribution
- Check quality metrics (focus, dx, angle, distance)
- Identify class distribution changes
- Generate montages for visual review
- Document findings per quarter

**Deliverables:**
- Assessment report: gamma3 accuracy estimate on 2024-2025 data
- Class distribution changes identified
- Filter threshold validation (are current thresholds still good?)
- Decision: Proceed with full backlog or adjust approach

### Phase 2: Deploy Infrastructure to Deepend (Week 2)

**Goal:** Validate deployment automation works end-to-end

```bash
# On buzz (ansible control node)
cd ~/sentinelcam/devops/ansible

# 1. Ensure deepend has SSH keys for datasink access
ansible-playbook playbooks/deploy-ssh-keys.yaml --limit=ml_trainers

# 2. Deploy deepthink role (notebooks, scripts, venv setup)
ansible-playbook playbooks/deploy-deepthink.yaml

# 3. Verify deployment
ansible ml_trainers -m command -a "ls -la /home/pyimagesearch/deepthink"
ansible ml_trainers -m command -a "/home/pyimagesearch/.virtualenvs/ml-training/bin/python --version"
ansible ml_trainers -m command -a "/home/pyimagesearch/.virtualenvs/ml-training/bin/pip list | grep imbalanced-learn"
```

**Verification:**
- Notebooks deployed to deepend:/home/pyimagesearch/deepthink/notebooks
- Scripts deployed: train_face_model.sh, upload_and_deploy.sh
- Venv has all packages (including imbalanced-learn)
- Can rsync from data1 datasink

### Phase 3: Incremental Backlog Processing (Weeks 3-8)

**Goal:** Curate 24 months of data in manageable segments

**Workflow per Quarter (repeat 8 times):**

1. **Submit FaceSweep for quarter dates**
   ```bash
   python face_sweep_dates.py --mode range \
     --start 2024-01-01 --end 2024-03-31 \
     --sentinel tcp://sentinel:5566
   ```

2. **Download and curate**
   ```python
   from sentinelcam.face_curation import FaceCurationWorkflow
   workflow = FaceCurationWorkflow()
   
   workflow.download_facelist('facelist_work.csv')
   
   # Work in notebook: filter, review montages, select candidates
   # Process 500-1000 candidates per session
   
   workflow.upload_facelist('facelist_work.csv')
   workflow.checkpoint_session(dates_processed=['2024-01-15', '2024-02-10'], 
                                notes='Q1 2024 complete')
   ```

3. **Track progress**
   ```python
   workflow.print_status()
   ```

**Curation Guidelines:**
- Process 500-1000 candidates per sitting
- Checkpoint after each quarter
- Upload interim facelist to sentinel regularly
- Document any data quality issues

**Final Step - Preserve Curated Facelist:**
```bash
# After all quarters complete, upload final facelist to datasink
# (Manual until automated artifact management is built)
ssh ops@sentinel
rsync -avz /home/ops/sentinelcam_data/facelist.csv \
  ops@data1:/home/ops/sentinelcam/model_registry/face_recognition/facelist_gamma4_curated.csv
```

### Phase 4: Gamma4 Training (Week 9)

**Goal:** Train class-balanced model with 24 months of curated data

1. **Trigger FaceDataUpdate on sentinel**
   ```bash
   # After final facelist upload
   # Submit FaceDataUpdate task (generates facedata.hdf5 from facelist.csv status=1)
   # This creates embeddings for all selected face candidates
   ```

2. **Transfer training artifacts to datasink**
   ```bash
   # MANUAL STEP (until automated artifact management exists)
   # After FaceDataUpdate completes, preserve artifacts centrally
   
   ssh ops@sentinel
   cd /home/ops/sentinel/models/face_recognition/2025-02-25
   
   # Upload to datasink model registry staging area
   rsync -avz facedata.hdf5 facelist.csv \
     ops@data1:/home/ops/sentinelcam/model_registry/face_recognition/gamma4_staging/
   
   # Verify transfer
   ssh ops@data1 ls -lh /home/ops/sentinelcam/model_registry/face_recognition/gamma4_staging/
   ```

   **Note:** Future automation will handle this via nightly sentinel playbook:
   - Cleanup daily data collection
   - Perform conditional sweeps
   - Upload artifacts (facedata.hdf5, facelist.csv) to datasink automatically
   - Trigger downstream processing

3. **Pull data to deepend for training**
   ```bash
   # SSH to deepend (Jetson Nano)
   ssh pyimagesearch@deepend
   
   # Pull training data from datasink (not from sentinel directly)
   rsync -avz ops@data1:/home/ops/sentinelcam/model_registry/face_recognition/gamma4_staging/ \
     /home/pyimagesearch/deepthink/data/
   
   # Verify files
   ls -lh /home/pyimagesearch/deepthink/data/
   ```

4. **Run training on Jetson Nano**
   ```bash
   # Start training (6hr overnight run)
   /home/pyimagesearch/deepthink/train_face_model.sh 2026-01-15
   
   # Monitor progress
   tail -f ~/sentinelcam/logs/training_2026-01-15.log
   ```

5. **Deploy via model registry**
   ```bash
   # After training completes
   /home/pyimagesearch/deepthink/upload_and_deploy.sh face_recognition 2026-01-15 \
     facemodel.pickle baselines.hdf5 facelist.csv facedata.hdf5
   
   # This will:
   # - Upload to data1:/home/ops/sentinelcam/model_registry/face_recognition/2026-01-15/
   # - Update model_registry.yaml: current_version: "2026-01-15"
   # - Trigger ansible deployment to sentinels
   ```

### Phase 5: Monitor and Iterate (Weeks 10+)

**Goal:** Validate gamma4 performance and establish ongoing workflow

1. **Production monitoring**
   - Run gamma4 for 1-2 weeks
   - Sample face recognition results daily
   - Track unknown rates, confidence distributions
   - Compare to gamma3 baseline

2. **Extract and persist unknowns**
   ```python
   # In analysis notebook
   # Query face recognition CSVs for high-quality unknowns
   # Cluster embeddings via DBSCAN
   # Save to data1:/home/ops/sentinelcam/model_registry/face_recognition/unknowns/2026-01-31.hdf5
   ```

3. **Establish incremental update cadence**
   - FaceSweep runs nightly via cron (future)
   - Candidates accumulate in facelist
   - Review quarterly or when threshold reached
   - Incremental retraining with warm-start

## Class Distribution Baseline

**Gamma3 (as of Feb 2025):**
- Mark: 391 images (~94%)
- Pen: Unknown count
- Michelle: 8 images (~2%)

**Gamma4 Goals:**
- Reduce imbalance via SMOTE
- Add any new faces discovered in 24-month gap
- Improve minority class accuracy (Michelle, others)

## Key Files and Locations

| File | Location | Purpose |
|------|----------|---------|
| `face_sweep_dates.py` | twister/ | Submit FaceSweep tasks |
| `face_curation.py` | twister/sentinelcam/ | Facelist sync workflow |
| `facemodel_build_beta.ipynb` | twister/ | Training template (model registry compatible) |
| `train_face_model.sh` | deepend:/home/pyimagesearch/deepthink/ | Training script |
| `upload_and_deploy.sh` | deepend:/home/pyimagesearch/deepthink/ | Registry deployment |
| `facelist.csv` | sentinel:/home/ops/sentinel/models/face_recognition/ | Training selections |
| `facedata.hdf5` | sentinel → deepend transfer | OpenFace embeddings |
| `model_registry.yaml` | devops/ansible/inventory/group_vars/all/ | Version tracking |

## Commands Quick Reference

```bash
# Submit FaceSweep
python face_sweep_dates.py --mode sample
python face_sweep_dates.py --mode range --start 2024-01-01 --end 2024-03-31

# Facelist management
python -m sentinelcam.face_curation download
python -m sentinelcam.face_curation upload  
python -m sentinelcam.face_curation status

# Ansible deployment
ansible-playbook playbooks/deploy-deepthink.yaml
ansible-playbook playbooks/deploy-ssh-keys.yaml --limit=ml_trainers

# Training on deepend
ssh pyimagesearch@deepend
/home/pyimagesearch/deepthink/train_face_model.sh 2026-01-15

# Deploy model
/home/pyimagesearch/deepthink/upload_and_deploy.sh face_recognition 2026-01-15 \
  facemodel.pickle baselines.hdf5 facelist.csv facedata.hdf5
```

## Success Criteria

- [ ] Sample assessment completed (gamma3 baseline on 2024-2025 data)
- [ ] Infrastructure deployed and validated on deepend
- [ ] 24-month backlog curated (all quarters processed)
- [ ] Gamma4 trained with class balancing
- [ ] Gamma4 deployed via model registry
- [ ] 2+ weeks production monitoring shows improvement
- [ ] Unknown persistence workflow established

## Notes

- Jetson Nano training: ~6 hours for full GridSearchCV
- Acceptable for infrequent retraining (quarterly updates)
- All ML artifacts persist on data1 datasink (not Jetson microSD)
- State files (curation progress) remain in twister workspace
- Model registry provides version tracking and rollback capability
