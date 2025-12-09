#!/usr/bin/env python3
"""
SentinelCam Component-Based Deployment Tool

A cross-platform deployment solution for selectively deploying SentinelCam
components to the internal network via bastion host.

Usage:
    # Deploy specific components
    python deploy.py imagenode sentinel
    
    # Deploy with dry-run
    python deploy.py --dry-run imagenode
    
    # Deploy all components
    python deploy.py --all
    
    # List available components
    python deploy.py --list
    
    # Deploy with custom config
    python deploy.py --config custom.yaml sentinel watchtower
"""

import argparse
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Set, Dict, Optional
import yaml


@dataclass
class DeploymentComponent:
    """Defines a deployable component with its associated files."""
    name: str
    description: str
    paths: List[str]
    required_files: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    ansible_playbook: Optional[str] = None
    

@dataclass
class DeploymentConfig:
    """Configuration for deployment process."""
    bastion_user: str = "rocky"
    bastion_host: str = "chandler-gate.local"
    transfer_cache: str = "~/transfer_cache/incoming"
    processing_script: str = "~/scripts/process-code-upload.sh"
    project_root: Path = Path(".")
    
    # File inclusion patterns
    include_extensions: List[str] = field(default_factory=lambda: [
        "*.py", "*.sh", "*.bash", "*.yaml", "*.yml", "*.json", 
        "*.txt", "*.rst", "*.md", "*.service", "*.timer", "*.j2",
        "*.cfg", "*.conf", "*.template", "*.sql"
    ])
    
    # Global exclusion patterns
    exclude_patterns: List[str] = field(default_factory=lambda: [
        ".git", "__pycache__", ".venv", ".vscode", "node_modules",
        "test_results", ".pytest_cache", "*.pyc", "*.pyo", "*.pyd",
        ".DS_Store", "*.bak", "*.tmp", "*.log", "*-OLD.*",
        ".backup", "backup", "*.swp", "*.swo"
    ])


class DeploymentTool:
    """Manages component-based deployments to internal network."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize deployment tool with configuration."""
        self.config = DeploymentConfig()
        self.components: Dict[str, DeploymentComponent] = {}
        self.load_component_config(config_path)
        
    def load_component_config(self, config_path: Optional[Path] = None):
        """Load component definitions from YAML configuration."""
        if config_path is None:
            config_path = Path(__file__).parent / "deployment-config.yaml"
        
        if not config_path.exists():
            # Use default component definitions
            self._load_default_components()
            return
            
        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
                
            # Load deployment settings if present
            if 'deployment' in config_data:
                dep_config = config_data['deployment']
                for key, value in dep_config.items():
                    if hasattr(self.config, key):
                        setattr(self.config, key, value)
            
            # Load component definitions
            if 'components' in config_data:
                for comp_data in config_data['components']:
                    component = DeploymentComponent(
                        name=comp_data['name'],
                        description=comp_data.get('description', ''),
                        paths=comp_data.get('paths', []),
                        required_files=comp_data.get('required_files', []),
                        exclude_patterns=comp_data.get('exclude_patterns', []),
                        ansible_playbook=comp_data.get('ansible_playbook')
                    )
                    self.components[component.name] = component
        except Exception as e:
            print(f"Warning: Failed to load config from {config_path}: {e}")
            print("Using default component definitions")
            self._load_default_components()
    
    def _load_default_components(self):
        """Load default component definitions."""
        self.components = {
            'imagenode': DeploymentComponent(
                name='imagenode',
                description='Image capture nodes (outposts)',
                paths=['imagenode/'],
                required_files=['imagenode/imagenode.py'],
                ansible_playbook='deploy-outpost-code-only.yaml'
            ),
            'sentinel': DeploymentComponent(
                name='sentinel',
                description='Sentinel monitoring service',
                paths=['sentinel/'],
                required_files=['sentinel/sentinel.py'],
                ansible_playbook='deploy-sentinel-code-only.yaml'
            ),
            'watchtower': DeploymentComponent(
                name='watchtower',
                description='Watchtower analysis service',
                paths=['watchtower/'],
                required_files=['watchtower/watchtower.py'],
                ansible_playbook='deploy-watchtower-code-only.yaml'
            ),
            'camwatcher': DeploymentComponent(
                name='camwatcher',
                description='Camera monitoring web interface',
                paths=['camwatcher/'],
                ansible_playbook='deploy-camwatcher-code-only.yaml'
            ),
            'imagehub': DeploymentComponent(
                name='imagehub',
                description='Central image hub service',
                paths=['imagehub/'],
                required_files=['imagehub/imagehub.py'],
                ansible_playbook='deploy-imagehub.yaml'
            ),
            'devops': DeploymentComponent(
                name='devops',
                description='DevOps scripts and Ansible playbooks',
                paths=['devops/'],
                exclude_patterns=['devops/test_results/*', 'devops/.backup/*']
            ),
            'config': DeploymentComponent(
                name='config',
                description='Configuration files (YAML)',
                paths=['*.yaml', '*.yml'],
                exclude_patterns=['deployment-config.yaml', 'workspace.code-workspace']
            ),
            'requirements': DeploymentComponent(
                name='requirements',
                description='Python dependencies',
                paths=['requirements.txt']
            ),
        }
    
    def list_components(self):
        """Display available components."""
        print("\n" + "-"*60)
        print("Available Deployment Components")
        print("-"*60 + "\n")
        
        for name, component in sorted(self.components.items()):
            print(f"  {name:15} - {component.description}")
            if component.ansible_playbook:
                print(f"  {'':15}   Playbook: {component.ansible_playbook}")
        
        print("\n" + "-"*60)
        print("Usage Examples:")
        print("  python deploy.py imagenode sentinel")
        print("  python deploy.py --all")
        print("  python deploy.py --dry-run devops")
        print("-"*60 + "\n")
    
    def validate_components(self, component_names: List[str]) -> List[str]:
        """Validate requested components and return valid names."""
        valid = []
        invalid = []
        
        for name in component_names:
            if name in self.components:
                valid.append(name)
            else:
                invalid.append(name)
        
        if invalid:
            print(f"Error: Unknown components: {', '.join(invalid)}")
            print("Use --list to see available components")
            sys.exit(1)
        
        return valid
    
    def check_required_files(self, components: List[str]) -> bool:
        """Check that all required files exist for selected components."""
        missing = []
        
        for comp_name in components:
            component = self.components[comp_name]
            for req_file in component.required_files:
                file_path = self.config.project_root / req_file
                if not file_path.exists():
                    missing.append(f"{comp_name}: {req_file}")
        
        if missing:
            print("Error: Required files not found:")
            for item in missing:
                print(f"  - {item}")
            return False
        
        return True
    
    def collect_files(self, components: List[str]) -> Set[Path]:
        """Collect all files to include in deployment package."""
        files_to_deploy = set()
        
        for comp_name in components:
            component = self.components[comp_name]
            
            for path_pattern in component.paths:
                path_obj = self.config.project_root / path_pattern
                
                # Handle glob patterns
                if '*' in path_pattern:
                    for matched_path in self.config.project_root.glob(path_pattern):
                        if matched_path.is_file():
                            files_to_deploy.add(matched_path)
                        elif matched_path.is_dir():
                            files_to_deploy.update(self._collect_from_directory(
                                matched_path, component.exclude_patterns
                            ))
                # Handle direct paths
                elif path_obj.exists():
                    if path_obj.is_file():
                        files_to_deploy.add(path_obj)
                    else:
                        files_to_deploy.update(self._collect_from_directory(
                            path_obj, component.exclude_patterns
                        ))
        
        return files_to_deploy
    
    def _collect_from_directory(self, directory: Path, 
                                component_excludes: List[str]) -> Set[Path]:
        """Recursively collect files from directory with filtering."""
        collected = set()
        all_excludes = self.config.exclude_patterns + component_excludes
        
        for ext_pattern in self.config.include_extensions:
            for file_path in directory.rglob(ext_pattern):
                if file_path.is_file():
                    # Check exclusion patterns
                    relative_path = file_path.relative_to(self.config.project_root)
                    if not self._is_excluded(relative_path, all_excludes):
                        collected.add(file_path)
        
        return collected
    
    def _is_excluded(self, path: Path, exclude_patterns: List[str]) -> bool:
        """Check if path matches any exclusion pattern."""
        path_str = str(path).replace('\\', '/')
        
        for pattern in exclude_patterns:
            # Simple pattern matching
            if pattern in path_str:
                return True
            # Wildcard patterns
            if '*' in pattern:
                import fnmatch
                if fnmatch.fnmatch(path_str, pattern):
                    return True
        
        return False
    
    def create_deployment_package(self, components: List[str], 
                                 dry_run: bool = False) -> Optional[Path]:
        """Create ZIP package with selected components."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        component_tag = "-".join(sorted(components)[:3])  # First 3 components
        if len(components) > 3:
            component_tag += "-plus"
        
        package_name = f"sentinelcam-{component_tag}-{timestamp}.zip"
        package_path = self.config.project_root / package_name
        
        print("\n" + "-"*60)
        print("Creating Deployment Package")
        print("-"*60)
        print(f"Components: {', '.join(components)}")
        print(f"Package:    {package_name}")
        
        # Collect files
        files = self.collect_files(components)
        
        if not files:
            print("\nError: No files found for selected components")
            return None
        
        total_size = sum(f.stat().st_size for f in files)
        size_mb = total_size / (1024 * 1024)
        
        print(f"Files:      {len(files)}")
        print(f"Size:       {size_mb:.2f} MB")
        
        if dry_run:
            print("\n[DRY RUN] Files that would be included:")
            for f in sorted(files)[:20]:  # Show first 20
                rel_path = f.relative_to(self.config.project_root)
                print(f"  {rel_path}")
            if len(files) > 20:
                print(f"  ... and {len(files) - 20} more files")
            print("\n[DRY RUN] Package creation skipped")
            return None
        
        # Create ZIP file
        print("\nCreating archive...")
        with zipfile.ZipFile(package_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(files):
                arcname = file_path.relative_to(self.config.project_root)
                zf.write(file_path, arcname)
        
        print(f"[OK] Package created: {package_path.name}")
        return package_path
    
    def upload_to_bastion(self, package_path: Path, dry_run: bool = False) -> bool:
        """Upload package to bastion host."""
        print("\n" + "-"*60)
        print("Uploading to Bastion")
        print("-"*60)
        print(f"Host: {self.config.bastion_user}@{self.config.bastion_host}")
        print(f"Dest: {self.config.transfer_cache}")
        
        if dry_run:
            print("\n[DRY RUN] Upload skipped")
            return True
        
        remote_path = f"{self.config.bastion_user}@{self.config.bastion_host}:{self.config.transfer_cache}/"
        
        try:
            cmd = ["scp", str(package_path), remote_path]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print("[OK] Upload complete")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Upload failed: {e}")
            if e.stderr:
                print(e.stderr)
            return False
        except FileNotFoundError:
            print("[ERROR] 'scp' command not found. Install OpenSSH client.")
            return False
    
    def trigger_internal_processing(self, package_name: str, 
                                   dry_run: bool = False) -> bool:
        """Trigger processing script on bastion."""
        print("\n" + "-"*60)
        print("Triggering Internal Processing")
        print("-"*60)
        
        if dry_run:
            print("\n[DRY RUN] Processing trigger skipped")
            return True
        
        remote_cmd = f"{self.config.processing_script} {package_name}"
        ssh_target = f"{self.config.bastion_user}@{self.config.bastion_host}"
        
        try:
            cmd = ["ssh", ssh_target, remote_cmd]
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print("[OK] Processing initiated")
            if result.stdout:
                print("\nBastion output:")
                print(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            print(f"[WARNING] Processing may have issues: {e}")
            if e.stderr:
                print(e.stderr)
            return False
    
    def cleanup_local_package(self, package_path: Path, dry_run: bool = False):
        """Remove local deployment package."""
        if dry_run:
            return
        
        try:
            package_path.unlink()
            print(f"\n[OK] Cleaned up local package: {package_path.name}")
        except Exception as e:
            print(f"\n[WARNING] Failed to cleanup package: {e}")
    
    def deploy(self, components: List[str], dry_run: bool = False, 
               keep_package: bool = False):
        """Execute complete deployment workflow."""
        print("\n" + "-"*60)
        print("SentinelCam Component Deployment")
        print("-"*60)
        
        if dry_run:
            print("[DRY RUN MODE - No changes will be made]")
        
        # Validate components
        valid_components = self.validate_components(components)
        
        # Check required files
        if not self.check_required_files(valid_components):
            sys.exit(1)
        
        # Create package
        package_path = self.create_deployment_package(valid_components, dry_run)
        if package_path is None:
            return
        
        # Upload to bastion
        if not self.upload_to_bastion(package_path, dry_run):
            self.cleanup_local_package(package_path, dry_run)
            sys.exit(1)
        
        # Trigger processing
        self.trigger_internal_processing(package_path.name, dry_run)
        
        # Cleanup
        if not keep_package:
            self.cleanup_local_package(package_path, dry_run)
        
        # Summary
        print("\n" + "-"*60)
        print("Deployment Summary")
        print("-"*60)
        print("[+] Package created and uploaded")
        print("[+] Internal processing initiated")
        print("[>] Code transfer to data sink in progress")
        print("[>] Ramrod will deploy via Ansible")
        
        # Show relevant playbooks
        playbooks = []
        for comp in valid_components:
            if self.components[comp].ansible_playbook:
                playbooks.append(self.components[comp].ansible_playbook)
        
        if playbooks:
            print("\nRelevant Ansible playbooks on the ramrod:")
            for pb in playbooks:
                print(f"  - {pb}")
        
        print("\nMonitoring commands:")
        print(f"  ssh {self.config.bastion_user}@{self.config.bastion_host} \\")
        print(f"    'tail -f ~/transfer_cache/logs/deployment.log'")
        print("-"*60 + "\n")


def main():
    """Main entry point for deployment tool."""
    parser = argparse.ArgumentParser(
        description="SentinelCam Component-Based Deployment Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s imagenode sentinel        Deploy specific components
  %(prog)s --all                     Deploy all components
  %(prog)s --list                    List available components
  %(prog)s --dry-run devops          Show what would be deployed
  %(prog)s --config custom.yaml imagenode  Use custom config
        """
    )
    
    parser.add_argument(
        'components',
        nargs='*',
        help='Components to deploy (use --list to see available)'
    )
    
    parser.add_argument(
        '--all',
        action='store_true',
        help='Deploy all components'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available components and exit'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deployed without making changes'
    )
    
    parser.add_argument(
        '--keep-package',
        action='store_true',
        help='Keep local package file after deployment'
    )
    
    parser.add_argument(
        '--config',
        type=Path,
        help='Path to custom deployment configuration file'
    )
    
    args = parser.parse_args()
    
    # Initialize tool
    try:
        tool = DeploymentTool(config_path=args.config)
    except Exception as e:
        print(f"Error initializing deployment tool: {e}")
        sys.exit(1)
    
    # Handle --list
    if args.list:
        tool.list_components()
        sys.exit(0)
    
    # Determine components to deploy
    if args.all:
        components = list(tool.components.keys())
    elif args.components:
        components = args.components
    else:
        print("Error: No components specified")
        print("Use --list to see available components or --all to deploy everything")
        parser.print_usage()
        sys.exit(1)
    
    # Execute deployment
    try:
        tool.deploy(components, dry_run=args.dry_run, keep_package=args.keep_package)
    except KeyboardInterrupt:
        print("\n\nDeployment cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nDeployment failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
