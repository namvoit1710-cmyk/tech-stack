import ast
import json
from app.layer1_domain.entities.planner import PlannerResponse, PlanStep


class PlannerParser:
 
    def _parse_response(self, response: str) -> PlannerResponse:
        try:
            data = self.normalize_planner_response(response)
 
            return PlannerResponse.from_dict(data)
 
        except Exception as e:
            raise ValueError(f"Failed to parse planner response: {e}")
       
 
    def normalize_planner_response(self, response: str) -> dict:
 
        # 1. Convert string -> Python object safely
        if isinstance(response, str):
            return json.loads(response)
 
        # 2. Expect list of blocks
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
 
                    # 3. First JSON parse attempt
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        pass
 
                    # 4. Fallback: handle escaped JSON edge cases
                    try:
                        cleaned = text.encode("utf-8").decode("unicode_escape")
                        return json.loads(cleaned)
                    except Exception as e:
                        raise ValueError(f"Failed to parse inner JSON:\n{text}") from e
 
            raise ValueError("No text block found")
 
        if isinstance(response, dict):
            return response
 
        raise ValueError(f"Unsupported type: {type(response)}")