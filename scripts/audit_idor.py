#!/usr/bin/env python3
"""
IDOR Vulnerability Audit Script
Scans all router files for potential IDOR vulnerabilities
"""

import os
import re
from pathlib import Path
from typing import List, Tuple

ROUTERS_DIR = Path("server_snapshot/routers")
PATTERNS = [
    # Pattern: db.query(...).filter(...id == ...).first()
    (r"\.filter\([^)]*\.id\s*==\s*[^)]*\)\.first\(\)", "Potential IDOR: Object fetched by ID without tenant check"),
    
    # Pattern: db.query().filter(...).first() followed by modification
    (r"\.first\(\)\s*\n\s+if\s+", "Potential IDOR: first() result used without validation"),
]

def scan_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """Scan a router file for IDOR patterns"""
    findings = []
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            lines = content.split('\n')
        
        # Check for tenant_id validation patterns
        has_tenant_check = 'tenant_id' in content
        
        # Find all .first() calls
        for i, line in enumerate(lines, 1):
            if '.first()' in line:
                # Check if previous lines contain tenant_id validation
                context_start = max(0, i - 10)
                context = '\n'.join(lines[context_start:i])
                
                # Look for tenant_id check within context
                tenant_pattern = r'\.tenant_id|tenant_id\s*='
                
                if not re.search(tenant_pattern, context):
                    findings.append((
                        i,
                        line.strip(),
                        "⚠️  POTENTIAL IDOR: .first() call without tenant_id validation in nearby context"
                    ))
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    
    return findings

def main():
    print("=" * 80)
    print("IDOR Vulnerability Audit")
    print("=" * 80)
    print()
    
    all_findings = {}
    total_issues = 0
    
    for router_file in sorted(ROUTERS_DIR.glob("*.py")):
        findings = scan_file(router_file)
        
        if findings:
            all_findings[router_file.name] = findings
            total_issues += len(findings)
            
            print(f"\n📄 {router_file.name}")
            print("-" * 80)
            for line_no, code, issue in findings:
                print(f"  Line {line_no}: {issue}")
                print(f"  Code: {code}")
                print()
    
    print("\n" + "=" * 80)
    print(f"SUMMARY: Found {total_issues} potential IDOR issues across {len(all_findings)} files")
    print("=" * 80)
    print()
    print("NEXT STEPS:")
    print("1. Review each finding manually")
    print("2. For each .first() call, verify tenant_id check:")
    print("   if obj and obj.tenant_id != current_user.tenant_id and current_user.role != 'superadmin':")
    print("       raise HTTPException(status_code=403, detail='Access denied')")
    print("3. Add tenant_id filtering to all queries:")
    print("   db.query(Model).filter(Model.id == id, Model.tenant_id == tenant_id).first()")
    print()

if __name__ == "__main__":
    main()
