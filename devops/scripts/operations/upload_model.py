#!/usr/bin/env python3
"""
Upload trained models from deepend to model registry
Usage: 
    python upload_model.py --name face_recognition --version v2.4 \
                           --source /path/to/model --framework torch
"""

import argparse
import hashlib
import shutil
import yaml
from pathlib import Path
from datetime import datetime

def calculate_checksum(file_path):
    """Calculate SHA256 checksum of file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def create_manifest(name, version, framework, source, files, target_nodes):
    """Create model manifest"""
    manifest = {
        'name': name,
        'version': version,
        'framework': framework,
        'source': source,
        'date_deployed': datetime.now().isoformat(),
        'target_nodes': target_nodes,
        'files': []
    }
    
    for file_path in files:
        manifest['files'].append({
            'name': file_path.name,
            'size': file_path.stat().st_size,
            'checksum': calculate_checksum(file_path)
        })
    
    return manifest

def upload_model(args):
    """Upload model to registry"""
    # Construct paths
    model_registry = Path(args.registry)
    source_path = Path(args.source)
    dest_path = model_registry / args.name / args.version
    
    # Create destination directory
    dest_path.mkdir(parents=True, exist_ok=True)
    
    # Copy model files
    files_copied = []
    if source_path.is_dir():
        for file_path in source_path.glob('*'):
            if file_path.is_file():
                shutil.copy2(file_path, dest_path / file_path.name)
                files_copied.append(dest_path / file_path.name)
                print(f"Copied: {file_path.name}")
    else:
        shutil.copy2(source_path, dest_path / source_path.name)
        files_copied.append(dest_path / source_path.name)
        print(f"Copied: {source_path.name}")
    
    # Create manifest
    manifest = create_manifest(
        name=args.name,
        version=args.version,
        framework=args.framework,
        source=args.source_origin,
        files=files_copied,
        target_nodes=args.targets
    )
    
    # Write manifest
    manifest_path = dest_path / 'manifest.yaml'
    with open(manifest_path, 'w') as f:
        yaml.dump(manifest, f, default_flow_style=False)
    
    print(f"\n✅ Model uploaded successfully!")
    print(f"   Registry: {dest_path}")
    print(f"   Files: {len(files_copied)}")
    print(f"   Manifest: {manifest_path}")
    print(f"\nNext steps:")
    print(f"1. Update model_versions.yaml:")
    print(f"   {args.name}_version: \"{args.version}\"")
    print(f"2. Deploy: ansible-playbook deploy_models.yaml --tags={args.name}")

def main():
    parser = argparse.ArgumentParser(description='Upload trained models to registry')
    parser.add_argument('--name', required=True, help='Model name (e.g., face_recognition)')
    parser.add_argument('--version', required=True, help='Model version (e.g., v2.4)')
    parser.add_argument('--source', required=True, help='Source file or directory path')
    parser.add_argument('--framework', required=True, 
                       choices=['torch', 'caffe', 'tensorflow', 'openvino', 'onnx'],
                       help='ML framework')
    parser.add_argument('--source-origin', default='deepend_training',
                       help='Origin of the model (e.g., deepend_training, external_zoo)')
    parser.add_argument('--targets', nargs='+', default=['sentinel', 'imagenode'],
                       help='Target node types')
    parser.add_argument('--registry', 
                       default='/home/ops/sentinelcam/model_registry',
                       help='Model registry path')
    
    args = parser.parse_args()
    upload_model(args)

if __name__ == '__main__':
    main()
