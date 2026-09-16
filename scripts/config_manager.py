#!/usr/bin/env python3
import os
import copy
import yaml
import logging
from scripts.utils.logging_utils import log_exception

logger = logging.getLogger(__name__)


class ConfigManager(dict):
    def __init__(self, data=None, config_path=None):
        super().__init__(data or {})
        self.config_path = config_path
        self._original_data = copy.deepcopy(dict(self)) if data else {}
    
    def set(self, key, value):
        if '.' in key:
            keys = key.split('.')
            current = self
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                elif not isinstance(current[k], dict):
                    current[k] = {}
                current = current[k]
            current[keys[-1]] = value
        else:
            self[key] = value
        logger.debug(f"Config set: {key} = {value}")
    
    def get_nested(self, key, default=None):
        if '.' in key:
            keys = key.split('.')
            current = self
            for k in keys:
                if isinstance(current, dict) and k in current:
                    current = current[k]
                else:
                    return default
            return current
        else:
            return self.get(key, default)
    
    def save(self, config_path=None):
        path = config_path or self.config_path
        if not path:
            logger.warning("No config path specified, cannot save configuration")
            return False
        
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
            with open(path, 'w') as file:
                yaml.safe_dump(dict(self), file, default_flow_style=False, indent=2)
            logger.info(f"Configuration saved to {path}")
            self._original_data = dict(self)
            return True
        except Exception as e:
            log_exception(logger, f"Failed to save configuration to {path}", e)
            return False
    
    def has_changes(self):
        return dict(self) != self._original_data
    
    def increment(self, key, amount=1):
        current = self.get_nested(key, 0)
        if isinstance(current, (int, float)):
            self.set(key, current + amount)
        else:
            logger.warning(f"Cannot increment non-numeric value at {key}: {current}")
    
    def append_to_list(self, key, value):
        current = self.get_nested(key, [])
        if isinstance(current, list):
            current.append(value)
            self.set(key, current)
        else:
            self.set(key, [value])
