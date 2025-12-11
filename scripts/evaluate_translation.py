#!/usr/bin/env python3
"""
Evaluate translation quality using multiple LLMs as judges.
"""

import json
import os
import sys
from typing import Dict
import requests
from dotenv import load_dotenv

load_dotenv()

# Model slugs for evaluation (all via OpenRouter)
# Note: Using only 2 models for faster batch QA
EVALUATOR_MODELS = [
    "z-ai/glm-4.6",                # GLM 4.6
    "qwen/qwen-max",               # Qwen Max
]

EVALUATION_PROMPT_ABSTRACT = """You are evaluating the quality of a Chinese-to-English translation of a scientific paper abstract.

**Original Chinese:**
{chinese}

**English Translation:**
{english}

Please rate the translation on a scale of 1-10 for each criterion:

1. **Accuracy** (1-10): How accurately does the translation convey the meaning of the original?
2. **Fluency** (1-10): How natural and fluent is the English?
3. **Terminology** (1-10): How appropriately are technical/scientific terms translated?
4. **Completeness** (1-10): Is all information from the original preserved?

Provide your ratings in this exact JSON format:
{{
  "accuracy": <score>,
  "fluency": <score>,
  "terminology": <score>,
  "completeness": <score>,
  "overall": <average>,
  "comments": "<brief explanation of strengths and weaknesses>"
}}"""

EVALUATION_PROMPT_FULL = """You are evaluating the quality and formatting of a translated scientific paper.

**English Translation (first 3000 chars):**
{english_preview}

Please rate the translation on a scale of 1-10 for each criterion:

1. **Accuracy** (1-10): Translation quality and meaning preservation
2. **Fluency** (1-10): Natural and readable English
3. **Terminology** (1-10): Appropriate technical/scientific terms
4. **Formatting** (1-10): Proper paragraph structure, section breaks, readability
5. **Organization** (1-10): Logical flow, clear structure, coherent sections

Provide your ratings in this exact JSON format:
{{
  "accuracy": <score>,
  "fluency": <score>,
  "terminology": <score>,
  "formatting": <score>,
  "organization": <score>,
  "overall": <average>,
  "comments": "<brief explanation of strengths and weaknesses>"
}}"""


def get_openrouter_key() -> str:
    """Get OpenRouter API key from environment."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set")
    return key


def evaluate_with_model(chinese: str, english: str, model: str, api_key: str, full_text: bool = False) -> Dict:
    """Have a model evaluate the translation."""
    if full_text:
        # For full text, just preview first 3000 chars
        preview = english[:3000] + ("..." if len(english) > 3000 else "")
        prompt = EVALUATION_PROMPT_FULL.format(english_preview=preview)
    else:
        prompt = EVALUATION_PROMPT_ABSTRACT.format(chinese=chinese, english=english)

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,  # Lower temp for more consistent evaluation
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

    resp.raise_for_status()
    result = resp.json()

    content = result['choices'][0]['message']['content']

    # Try to extract JSON from response
    try:
        # Look for JSON block in response
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content.strip()

        evaluation = json.loads(json_str)
        evaluation['model'] = model
        return evaluation
    except Exception as e:
        print(f"Failed to parse JSON from {model}: {e}")
        print(f"Response: {content}")
        return {
            "model": model,
            "error": str(e),
            "raw_response": content
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate translation quality")
    parser.add_argument("translated_path", help="Path to translated JSON file")
    parser.add_argument("--selected", default="data/selected.json", help="Path to selected.json")
    parser.add_argument("--store-db", action="store_true", help="Store results in SQLite database")
    args = parser.parse_args()

    translated_path = args.translated_path
    selected_path = args.selected

    # Load translated data
    with open(translated_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    paper_id = data['id']
    english_abstract = data.get('abstract_en', '')
    english_body = data.get('body_en', [])

    # Check if we have full text
    has_full_text = bool(english_body)

    # Load original Chinese from selected.json (only needed for abstract evaluation)
    chinese_abstract = ""
    if not has_full_text:
        with open(selected_path, 'r', encoding='utf-8') as f:
            selected_data = json.load(f)

        # Find matching record
        chinese_record = next((r for r in selected_data if r['id'] == paper_id), None)
        if not chinese_record:
            print(f"Error: Could not find {paper_id} in {selected_path}")
            sys.exit(1)

        # Extract just the Chinese abstract text (strip metadata)
        full_abstract = chinese_record.get('abstract', '')
        # The abstract contains: Chinese, then English title, then metadata, then "摘要:" and the actual abstract
        if '摘要:' in full_abstract:
            chinese_abstract = full_abstract.split('摘要:')[1].split('来自：')[0].strip()
        else:
            chinese_abstract = full_abstract

        if not chinese_abstract or not english_abstract:
            print("Error: Missing Chinese abstract or English translation")
            sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Evaluating translation for: {paper_id}")
    if has_full_text:
        print(f"Mode: FULL TEXT ({len(english_body)} paragraphs)")
    else:
        print("Mode: ABSTRACT ONLY")
    print(f"{'='*60}\n")

    api_key = get_openrouter_key()
    evaluations = []

    # Prepare evaluation text
    if has_full_text:
        # Convert body paragraphs to markdown for evaluation
        from src.format_translation import format_translation_to_markdown
        eval_text = format_translation_to_markdown(data)
    else:
        eval_text = english_abstract

    for model in EVALUATOR_MODELS:
        print(f"Evaluating with {model}...")
        try:
            result = evaluate_with_model(chinese_abstract, eval_text, model, api_key, full_text=has_full_text)
            evaluations.append(result)

            if 'error' not in result:
                print(f"  Overall: {result.get('overall', 'N/A')}/10")
                print(f"  Accuracy: {result.get('accuracy', 'N/A')}/10")
                print(f"  Comments: {result.get('comments', 'N/A')[:80]}...")
            else:
                print(f"  Error: {result['error']}")
        except Exception as e:
            print(f"  Failed: {e}")
            evaluations.append({
                "model": model,
                "error": str(e)
            })
        print()

    # Save results
    output_dir = "data/evaluations"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{paper_id}_eval.json")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            "paper_id": paper_id,
            "chinese_abstract": chinese_abstract,
            "english_translation": english_abstract,
            "evaluations": evaluations
        }, f, indent=2, ensure_ascii=False)

    print(f"Results saved to: {output_path}")

    # Store in database if requested
    if args.store_db:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from src import job_queue

        for eval_result in evaluations:
            if 'error' not in eval_result and 'overall' in eval_result:
                job_queue.save_qa_result(
                    paper_id=paper_id,
                    model=eval_result.get('model', 'unknown'),
                    overall=eval_result.get('overall', 0.0),
                    accuracy=eval_result.get('accuracy', 0.0),
                    fluency=eval_result.get('fluency', 0.0),
                    terminology=eval_result.get('terminology', 0.0),
                    completeness=eval_result.get('completeness', 0.0),
                    formatting=eval_result.get('formatting', 0.0),
                    organization=eval_result.get('organization', 0.0),
                    comments=eval_result.get('comments', '')
                )
        print(f"Stored {len([e for e in evaluations if 'error' not in e])} evaluations in database")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}\n")

    avg_scores = {}
    for eval_result in evaluations:
        if 'error' not in eval_result and 'overall' in eval_result:
            for key in ['accuracy', 'fluency', 'terminology', 'completeness', 'formatting', 'organization', 'overall']:
                if key in eval_result:
                    avg_scores.setdefault(key, []).append(eval_result[key])

    if avg_scores:
        for metric, scores in avg_scores.items():
            avg = sum(scores) / len(scores)
            print(f"{metric.capitalize():15} {avg:.1f}/10 (from {len(scores)} models)")


if __name__ == "__main__":
    main()
