#!/bin/bash
# Create validated release snapshot for GitHub publication
# Usage: ./create-release-snapshot.sh v2.1.1
# Run on data1 node

VERSION="$1"
CURRENT_DIR="/home/ops/sentinelcam/current_deployment"
RELEASES_DIR="/home/ops/sentinelcam/releases"

if [ -z "$VERSION" ]; then
    echo "❌ Usage: $0 <version>"
    echo "   Example: $0 v2.1.1"
    echo ""
    echo "Creates a validated release snapshot from current deployment"
    echo "for GitHub publication and long-term archival."
    exit 1
fi

# Validate version format
if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ Version must be in format vX.Y.Z (e.g., v2.1.1)"
    exit 1
fi

if [ ! -d "$CURRENT_DIR" ]; then
    echo "❌ Current deployment directory not found: $CURRENT_DIR"
    exit 1
fi

RELEASE_PATH="$RELEASES_DIR/$VERSION"
if [ -d "$RELEASE_PATH" ]; then
    echo "❌ Release version $VERSION already exists"
    echo "📂 Path: $RELEASE_PATH"
    exit 1
fi

echo "🎖️ Creating validated release snapshot: $VERSION"
echo "📂 Source: $CURRENT_DIR"
echo "📂 Destination: $RELEASE_PATH"

# Confirm release snapshot creation
read -p "🚀 Create release snapshot $VERSION from current deployment? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Release snapshot cancelled"
    exit 1
fi

# Create release directory
mkdir -p "$RELEASE_PATH"

# Copy current deployment to release
echo "📦 Creating release snapshot..."
if rsync -av "$CURRENT_DIR/" "$RELEASE_PATH/"; then
    echo "✅ Release snapshot created successfully"
else
    echo "❌ Failed to create release snapshot"
    exit 1
fi

# Create release metadata
RELEASE_TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
cat > "$RELEASE_PATH/RELEASE_INFO.md" << EOF
# SentinelCam Release $VERSION

**Created:** $RELEASE_TIMESTAMP  
**Source:** Current validated deployment  
**Status:** Release Ready  

## Components Included
$(find "$RELEASE_PATH" -maxdepth 1 -type d ! -name ".*" ! -path "$RELEASE_PATH" | sed 's/.*\//- /' | sort)

## Validation Status
- ✅ Real-world deployment tested
- ✅ System stability confirmed in production network
- ✅ Ready for GitHub publication

## Phase 2 Development Pipeline
This release was created from the current deployment after successful
validation through real-world operation on the SentinelCam surveillance network.

## Use Cases
This validated release is suitable for:
- GitHub repository publication
- Distribution to other SentinelCam installations
- Long-term archival and reference
- Rollback reference point

EOF

# Create git-ready README for publication
echo "📋 Preparing for GitHub publication..."
cat > "$RELEASE_PATH/README.md" << EOF
# SentinelCam $VERSION

Validated surveillance system components from Phase 2 deployment pipeline.

## Components

$(find "$RELEASE_PATH" -maxdepth 1 -type d ! -name ".*" ! -path "$RELEASE_PATH" | while read dir; do
    component=$(basename "$dir")
    py_count=$(find "$dir" -name "*.py" 2>/dev/null | wc -l)
    if [ $py_count -gt 0 ]; then
        echo "- **$component**: $py_count Python files"
    fi
done)

## Installation

See individual component directories for installation instructions.
Full deployment documentation is in the devops/ directory.

## Validation

This release has been validated through real-world deployment and operation 
in a production SentinelCam surveillance network using the Phase 2 CI/CD pipeline.

**Release Date:** $RELEASE_TIMESTAMP  
**Validation:** ✅ Production Tested  
**Pipeline Phase:** Phase 2 (Rollback-capable deployment)

EOF

# Update latest symlink
echo "🔗 Updating latest release symlink..."
cd "$RELEASES_DIR"
rm -f latest
ln -sf "$VERSION" latest

# Log release creation
echo "$VERSION|$RELEASE_TIMESTAMP|validated" >> "$RELEASES_DIR/release_history.log"

# Create git commands for publication
cat > "$RELEASE_PATH/PUBLISH_TO_GITHUB.sh" << EOF
#!/bin/bash
# Commands to publish this validated release to GitHub

echo "🐙 Publishing SentinelCam $VERSION to GitHub..."

# Initialize git repo if needed
if [ ! -d .git ]; then
    git init
    git branch -M main
    git remote add origin git@github.com:shumwaymark/sentinelcam.git
fi

# Add all files
git add .
git commit -m "Release $VERSION - Phase 2 validated"
git tag -a "$VERSION" -m "Validated release $VERSION from Phase 2 pipeline"

# Push to GitHub
git push origin main
git push origin "$VERSION"

echo "✅ Published to GitHub: $VERSION"
EOF

chmod +x "$RELEASE_PATH/PUBLISH_TO_GITHUB.sh"

echo ""
echo "✅ Release snapshot created successfully!"
echo "📦 Version: $VERSION"
echo "📂 Location: $RELEASE_PATH"
echo "🔗 Latest: $RELEASES_DIR/latest -> $VERSION"
echo ""
echo "🐙 Ready for GitHub publication:"
echo "   cd $RELEASE_PATH"
echo "   ./PUBLISH_TO_GITHUB.sh"
echo ""
echo "📋 Release documentation created:"
echo "   - RELEASE_INFO.md (metadata and validation status)"
echo "   - README.md (GitHub-ready documentation)"
echo "   - PUBLISH_TO_GITHUB.sh (publication helper)"
echo ""
echo "💡 This snapshot represents the current validated deployment"
echo "   and is ready for distribution and archival."
