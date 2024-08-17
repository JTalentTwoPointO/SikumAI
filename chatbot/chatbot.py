import logging
import os
import re

import requests
from dotenv import load_dotenv

from chatbot.prompt_generating import build_chapter_list_prompt, build_plot_points_prompt, build_bagrut_answers_prompt, \
    build_bagrut_questions_prompt
from database import PlotPoint
from functions.book import find_chapter
from functions.formatting import chapters_to_list
from functions.prompt_caching import get_prompt, save_prompt, save

# Load environment variables from ..env file
load_dotenv()


def get_chapter_list(book_name):
    prompt = build_chapter_list_prompt(book_name)
    result = execute_prompt(prompt)
    return chapters_to_list(result)


def execute_prompt(prompt, override: bool = False):
    """
    Generates a summary using the Google Language Model API.

    Parameters:
    prompt (str): The prompt to be sent to the API.

    Returns:
    str: The generated summary from the API.
    """
    cached_response = get_prompt(prompt)
    if cached_response is not None and not override:
        return cached_response.response

    google_api_key = os.getenv('GOOGLE_API_KEY')
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    params = {"key": google_api_key}
    tries, max_tries = 0, 3

    while tries < max_tries:
        print(f"Executing prompt {prompt[:40]}...")
        try:
            response = requests.post(url, headers=headers, params=params, json=payload)
            if response.status_code == 200:
                summary = response.json()['candidates'][0]['content']['parts'][0]['text']
                print("Raw API Response:\n\n", summary)
                save_prompt(prompt, summary)
                logging.debug(f"API Response:{summary}")
                return summary
            else:
                logging.warning(f"Unexpected status code {response.status_code}: {response.text}")
                tries += 1
        except Exception as e:
            logging.error(f"API Request failed with error: {e}")
            tries += 1

    return "Could not get answer from API, try again later"


def generate_plot_points(book_name, chapter_name):
    chapter_list = get_chapter_list(book_name)
    page_content = find_chapter(book_name, chapter_name, chapter_list)

    plot_points_prompt = build_plot_points_prompt(book_name, chapter_name, page_content)
    plot_points_response = execute_prompt(plot_points_prompt, override=True)

    if "Error" in plot_points_response:
        return None, {"error": plot_points_response}

    plot_points_data = parse_plot_points_response(plot_points_response)

    # Assuming chapter_number can be derived from chapter_name or needs to be passed as an argument
    chapter_number = chapter_list.index(chapter_name) + 1 if chapter_name in chapter_list else 0

    plot_point = PlotPoint(
        book_name=book_name,
        chapter_name=chapter_name,
        chapter_number=chapter_number,
        death_and_tragic_events=plot_points_data.get('death_and_tragic_events'),
        decisions=plot_points_data.get('decisions'),
        conflicts=plot_points_data.get('conflicts'),
        character_development=plot_points_data.get('character_development'),
        symbolism_and_imagery=plot_points_data.get('symbolism_and_imagery'),
        foreshadowing=plot_points_data.get('foreshadowing'),
        setting_description=plot_points_data.get('setting_description'),
        chapter_summary=plot_points_data.get('chapter_summary')
    )

    # print("PlotPoint Instance:", plot_point)
    # print("Plot points generated and saved successfully.\n\n")
    save(plot_point)
    return plot_point, plot_points_data


def parse_plot_points_response(response):
    logging.debug(f"Raw API Response: {response}")

    plot_points_data = {
        'death_and_tragic_events': '',
        'decisions': '',
        'conflicts': '',
        'character_development': '',
        'symbolism_and_imagery': '',
        'foreshadowing': '',
        'setting_description': '',
        'chapter_summary': ''
    }

    pattern = re.compile(r'\*\*([A-Za-z\s]+):\*\*')
    matches = list(pattern.finditer(response))
    sections = {match.group(1).strip().lower().replace(' ', '_'): match.start() for match in matches}

    logging.debug(f"Sections identified: {sections}")

    for i, section in enumerate(sections):
        start_pos = sections[section]
        end_pos = sections[list(sections.keys())[i + 1]] if i + 1 < len(sections) else len(response)
        content = response[start_pos:end_pos].split('**')[-1].strip()
        plot_points_data[section] = content

    for key in plot_points_data:
        plot_points_data[key] = plot_points_data[key].strip()

    logging.debug(f"Parsed Plot Points Data: {plot_points_data}")
    return plot_points_data


def parse_questions_and_answers(response_text):
    """
    Parses the AI's response to extract questions and answers separately.

    Parameters:
    response_text (str): The raw text response from the AI.

    Returns:
    list: A list of dictionaries, each containing a 'question' and an 'answer'.
    """
    questions_and_answers = []
    parts = re.split(r'(\*\*Question \d+:)', response_text)

    # Skipping the first element which will be the intro part before the first question
    for i in range(1, len(parts), 2):
        question = parts[i].strip()
        if i + 1 < len(parts):
            answer = parts[i + 1].split("**Answer:**")[1].strip() if '**Answer:**' in parts[i + 1] else ''
        else:
            answer = ''

        questions_and_answers.append({
            "question": question,
            "answer": answer
        })

    return questions_and_answers


def generate_bagrut_qa(book_name, chapter_name, plot_points_data):
    # Bagrut examples (leave as is)
    bagrut_examples = [
        {"question": "...", "type": "open-ended"},
        # Add more examples
    ]

    # Generate the Bagrut questions in one API call
    bagrut_questions_prompt = build_bagrut_questions_prompt(book_name, chapter_name, plot_points_data, bagrut_examples)
    bagrut_questions_response = execute_prompt(bagrut_questions_prompt)

    # Generate all Bagrut answers in one API call
    combined_questions = bagrut_questions_response
    bagrut_answers_prompt = build_bagrut_answers_prompt(book_name, chapter_name, plot_points_data, combined_questions)
    bagrut_answers_response = execute_prompt(bagrut_answers_prompt)

    # Parse the questions and answers
    questions_and_answers = parse_questions_and_answers(bagrut_answers_response)

    return questions_and_answers


def format_bagrut_output(questions_and_answers):
    """
    Formats the Bagrut Q&A output into a more readable format for front-end.

    Parameters:
    questions_and_answers (list): The list of dictionaries containing questions and answers.

    Returns:
    str: Formatted string output with Q and A.
    """
    formatted_output = []

    # Loop through each QA pair in the results
    for idx, qa_pair in enumerate(questions_and_answers, 1):
        question = qa_pair.get('question', '').strip()
        answer = qa_pair.get('answer', '').strip()

        # Only include if both question and answer exist
        if question and answer:
            formatted_output.append(f"Q: {question}")
            formatted_output.append(f"A: {answer}\n")

    # Join all parts into a single string for easier front-end rendering
    return "\n".join(formatted_output)


# Generate and format the final output
def generate_chapter_bagrutQnA(book_name, chapter):
    """
    Generates summaries, Plot Points & Bagrut Style Questions and Answers.

    Parameters:
    book_name (str): The name of the book.
    chapter (str): The chapter title.

    Returns:
    str: A formatted string containing all the plot points and Q&A.
    """

    # Generate plot points
    plot_point, plot_points_data = generate_plot_points(book_name, chapter)

    if plot_point is None:
        return f"Error generating plot points for {chapter}: {plot_points_data.get('error', 'Unknown error')}"

    # Generate Bagrut-style questions and answers using plot points
    questions_and_answers = generate_bagrut_qa(book_name, chapter, plot_points_data)

    # Format the output for Q&A
    formatted_results = format_bagrut_output(questions_and_answers)

    return formatted_results
