#!/usr/bin/env python3
"""
Context Rules for Adversarial Mode
Auto-loads failure patterns based on file type
"""

from pathlib import Path
from typing import Optional, Dict, Any


class ContextRulesEngine:
    """Load and apply context rules based on file being edited"""
    
    def __init__(self, context_dir: str = ".context"):
        """Initialize context rules engine"""
        self.context_dir = Path(context_dir)
        self.pattern_cache = {}
        self.load_patterns()
    
    def load_patterns(self):
        """Load all failure patterns into memory"""
        patterns_dir = self.context_dir / "failure_patterns"
        if not patterns_dir.exists():
            return

        for pattern_file in patterns_dir.glob("*.md"):
            pattern_name = pattern_file.stem
            try:
                self.pattern_cache[pattern_name] = pattern_file.read_text(encoding="utf-8")
            except Exception:
                self.pattern_cache[pattern_name] = pattern_file.read_text(encoding="utf-8", errors="replace")
    
    def get_context_for_file(self, filepath: str) -> str:
        """Get relevant context for a file based on type"""
        context_parts = []
        filepath_lower = filepath.lower()

        file_text = ""
        try:
            file_text = Path(filepath).read_text(encoding="utf-8")
        except Exception:
            try:
                file_text = Path(filepath).read_text(encoding="utf-8", errors="replace")
            except Exception:
                file_text = ""

        if filepath_lower.endswith((".cbl", ".cob", ".cobol")):
            cobol_pitfalls = self.pattern_cache.get("cobol_pitfalls")
            if cobol_pitfalls:
                context_parts.append(cobol_pitfalls)
            for name, content in self.pattern_cache.items():
                if "cobol" in name.lower() and content and content not in context_parts:
                    context_parts.append(content)

        if "packed_decimal" in file_text.lower() or "comp-3" in file_text.upper() or "comp3" in file_text.lower():
            starcoder_adv = self.pattern_cache.get("starcoder_adversarial")
            if starcoder_adv:
                context_parts.append(starcoder_adv)

        return "\n\n".join(context_parts)
    
    def should_warn_on_file(self, filepath: str) -> Optional[Dict[str, Any]]:
        """Determine if file should trigger warnings"""
        filepath_lower = filepath.lower()
        
        if filepath_lower.endswith(('.cbl', '.cob', '.cobol')):
            return {
                "should_warn": True,
                "warning_type": "COBOL_PACKED_DECIMAL",
                "severity": "high",
                "message": "⚠️  COBOL code detected. Known weakness in COMP-3 handling."
            }
        
        return None


def get_context_for_file(filepath: str) -> str:
    """Main entry point: Get context for a file"""
    engine = ContextRulesEngine()
    return engine.get_context_for_file(filepath)


def should_warn_on_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Main entry point: Determine if file should trigger warnings"""
    engine = ContextRulesEngine()
    return engine.should_warn_on_file(filepath)
