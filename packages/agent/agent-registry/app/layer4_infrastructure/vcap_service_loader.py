import base64
import json
import os
from pathlib import Path
from dotenv import load_dotenv

class VCAPServiceLoader:
    """
    Utility class to load VCAP services from environment variables or local config files.
    Handles both direct JSON and base64-encoded VCAP_SERVICES.
    """
    @staticmethod
    def load_vcap_service(require_db=False):
        """Load vcap service from both SAP or local"""
        load_dotenv()
        
        def _try_load_json(string_data):
            decoder = json.JSONDecoder()
            env_data, _ = decoder.raw_decode(string_data)
            return env_data

        vcap_service = os.getenv("VCAP_SERVICES")
        if not vcap_service:
            vcap_service = os.getenv("VCAP_INFO")

        if vcap_service:
            if vcap_service.startswith("base64"):
                # Extract the base64 data (handle "base64" or "base64:" prefix)
                encoded_data = vcap_service[6:].lstrip(':').strip()
                
                # Add padding if needed (base64 strings should be multiples of 4)
                padding_needed = len(encoded_data) % 4
                if padding_needed:
                    encoded_data += '=' * (4 - padding_needed)
                
                try:
                    decoded_bytes = base64.b64decode(encoded_data)
                    decoded_string = decoded_bytes.decode('utf-8')
                    json_vcap_service = _try_load_json(decoded_string)
                    if "VCAP_SERVICES" in json_vcap_service:
                        os.environ["VCAP_SERVICES"] = json.dumps(json_vcap_service["VCAP_SERVICES"])
                except (base64.binascii.Error, Exception) as e:
                    print(f"❌ Error decoding base64 VCAP_SERVICES: {e}")

            if require_db:
                vcap = os.getenv("VCAP_SERVICES")
                vcap = json.loads(vcap)
    
                if "hana" in vcap:
                    return
        
        if require_db or not vcap_service:
            cwd = Path(os.getcwd())
            default_env_path = cwd / "default-env.json"
            
            if default_env_path.exists():
                try:
                    with open(default_env_path, 'r', encoding='utf-8') as f:
                        file_content = f.read().strip()
                        json_vcap_service = _try_load_json(file_content)
                    
                    if "VCAP_SERVICES" in json_vcap_service:
                        os.environ["VCAP_SERVICES"] = json.dumps(json_vcap_service["VCAP_SERVICES"])
                except Exception as e:
                    print(f"❌ Error reading config file: {e}")