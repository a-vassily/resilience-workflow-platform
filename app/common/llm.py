from __future__ import annotations

import json
from typing import Any


RESPONSES_API_PATH_FRAGMENT = '/responses'


def uses_responses_api(llm_path: str) -> bool:
    return RESPONSES_API_PATH_FRAGMENT in llm_path.lower()



def build_lmstudio_request_body(
    llm_path: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> dict[str, Any]:
    if uses_responses_api(llm_path):
        return {
            'model': model,
            'input': [
                {
                    'role': 'system',
                    'content': [
                        {
                            'type': 'input_text',
                            'text': system_prompt,
                        }
                    ],
                },
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'input_text',
                            'text': user_prompt,
                        }
                    ],
                },
            ],
            'temperature': temperature,
            'stream': False,
            'text': {
                'format': {
                    'type': 'json_object',
                }
            },
        }

    return {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
        'stream': False,
        'response_format': {'type': 'json_object'},
    }



def extract_lmstudio_text(llm_path: str, raw_response: dict[str, Any]) -> str:
    if not uses_responses_api(llm_path):
        return raw_response['choices'][0]['message']['content']

    output_text = raw_response.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    for item in raw_response.get('output', []):
        if item.get('type') != 'message':
            continue
        for content_item in item.get('content', []):
            if content_item.get('type') in {'output_text', 'text'}:
                text = content_item.get('text')
                if isinstance(text, str) and text.strip():
                    return text

    raise KeyError('No text content found in LM Studio response')



def extract_lmstudio_json(llm_path: str, raw_response: dict[str, Any]) -> dict[str, Any]:
    content = extract_lmstudio_text(llm_path, raw_response)
    return json.loads(content)
