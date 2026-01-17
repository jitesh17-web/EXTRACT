import json
import requests
from io import BytesIO
import logging
import re
from bs4 import BeautifulSoup
import time
import socket
import aiohttp
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.error import Conflict, NetworkError, TimedOut

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Define conversation states
ASK_NID, ASK_PDF_NAME, CHOOSE_FORMAT, ASK_INFO_NID, ASK_AUTH_USER_ID = range(5)

# IMPORTANT: Replace with your actual bot token and authorized user IDs
BOT_TOKEN = "7569082224:AAHKhpg_MfaMbdXtLYiD2nVlVWXPN9kz4JU"
OWNER_ID = 7927314662  # The main owner who can authorize users
# NOTE: AUTHORIZED_USER_IDS is mutable and will be modified at runtime.
AUTHORIZED_USER_IDS = [7927314662, 8188515782, 7686927258, 8293981933]

def check_internet_connection():
    """Check if internet connection is available"""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False

def test_telegram_api(token):
    """Test if Telegram API is accessible"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(url, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram API test failed: {e}")
        return False

def clean_text_for_telegram(text):
    """Clean text to prevent Telegram parsing errors and normalize line endings."""
    if not text:
        return "N/A"
    
    # Convert to string and basic cleaning
    text = str(text).strip()
    
    # --- Line Ending Normalization ---
    # 1. Normalize all line endings to just '\n' (removes all '\r')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 2. Consolidate consecutive newlines into clean paragraph breaks (\n\n)
    text = re.sub(r'\n{2,}', '\n\n', text).strip()
    # --------------------------------
    
    # Remove HTML tags
    text = re.sub(r'<[^>]*>', '', text)
    
    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z0-9#]+;', '', text)
    
    # Remove problematic characters that can cause parsing issues
    text = re.sub(r'[^\w\s\-\.\(\),:\n/]', '', text)
    
    # Clean up excess whitespace (this happens after line normalization)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    # Limit length to prevent issues
    if len(text) > 800:
        text = text[:797] + "..."
    
    return text if text else "N/A"

def fetch_locale_json_from_api(nid: str):
    """Fetches question data from the API for a given NID with enhanced error handling."""
    url = f"https://learn.aakashitutor.com/quiz/{nid}/getlocalequestions"
    
    retry_configs = [
        {"timeout": 15, "headers": {}},
        {"timeout": 30, "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}},
        {"timeout": 45, "headers": {"User-Agent": "Python-requests/2.28.0"}},
    ]
    
    for i, config in enumerate(retry_configs):
        try:
            logger.info(f"Attempt {i+1} to fetch data for NID {nid}")
            response = requests.get(url, timeout=config["timeout"], headers=config["headers"])
            response.raise_for_status()
            raw_data = response.json()
            logger.info(f"Successfully fetched data for NID {nid} on attempt {i+1}")

            processed_questions = []

            def is_valid_question_object(data_obj):
                return isinstance(data_obj, dict) and \
                       "body" in data_obj and \
                       "alternatives" in data_obj and \
                       isinstance(data_obj.get("alternatives"), list)

            if isinstance(raw_data, dict):
                for question_nid_key, question_data_by_language in raw_data.items():
                    if isinstance(question_data_by_language, dict):
                        english_version = question_data_by_language.get("843")
                        
                        if is_valid_question_object(english_version):
                            # The fields here are crucial for syllabus extraction
                            question_data = {
                                "body": english_version.get("body", ""),
                                "alternatives": english_version.get("alternatives", []),
                                "hint": english_version.get("hint", ""),
                                "solution": english_version.get("solution", ""),
                                "detailed_solution": english_version.get("detailed_solution", ""),
                                "explanation": english_version.get("explanation", ""),
                                "chapter": english_version.get("chapter", ""),
                                "chapter_name": english_version.get("chapter_name", ""),
                                "subject": english_version.get("subject", ""),
                                "subject_name": english_version.get("subject_name", ""), # Used for syllabus grouping
                                "topic": english_version.get("topic", ""),
                                "topic_name": english_version.get("topic_name", ""), # Used for syllabus grouping
                                "subtopic": english_version.get("subtopic", ""),
                                "subtopic_name": english_version.get("subtopic_name", ""),
                                "difficulty_level": english_version.get("difficulty_level", ""),
                                "bloom_taxonomy": english_version.get("bloom_taxonomy", ""),
                                "question_type": english_version.get("question_type", ""),
                            }
                            processed_questions.append(question_data)

            if processed_questions:
                return processed_questions
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error on attempt {i+1} for NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout on attempt {i+1} for NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error on attempt {i+1} for NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error on attempt {i+1} for NID {nid}: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error on attempt {i+1} for NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)

    logger.error(f"All attempts failed for NID {nid}")
    return None

def fetch_test_metadata(nid: str):
    """
    Fetches the full test metadata (title, description, syllabus) 
    from the getquizfromid API.
    """
    # This is the correct API endpoint for test metadata
    url = f"https://learn.aakashitutor.com/api/getquizfromid?nid={nid}"
    
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                # Returns the full metadata object which contains title, description, syllabus, etc.
                return data[0] 
            return None
        except Exception as e:
            logger.error(f"Error fetching metadata on attempt {attempt+1} for NID {nid}: {e}")
            if attempt < 2:
                time.sleep(2)
    
    return None

async def fetch_quiz_info(nid):
    """Fetch quiz information for info command - calls fetch_test_metadata internally"""
    return fetch_test_metadata(nid)

def format_timestamp(timestamp):
    """Convert timestamp to readable date format"""
    try:
        if timestamp:
            dt = datetime.fromtimestamp(int(timestamp))
            return dt.strftime("%d %b %Y, %I:%M %p")
        return "N/A"
    except:
        return "N/A"

def clean_solution_content(content: str) -> str:
    """
    Cleans solution content by removing NID numbers, JSON-like artifacts, 
    and aggressively removing escaped line breaks.
    """
    if not content or content is None:
        return ""
    
    content_str = str(content)
    
    # 1. Remove JSON artifacts
    content_str = re.sub(r'\{[\'"]nid[\'"]:\s*[\'"][0-9]+[\'"],\s*[\'"]content[\'"]:\s*[\'"]', '', content_str)
    content_str = re.sub(r',\s*[\'"]clipping_nid[\'"]:\s*None,\s*[\'"]type[\'"]:\s*[\'"]HTML5[\'"],\s*[\'"]duration[\'"]:\s*None\}.*?$', '', content_str)
    content_str = re.sub(r'\{[^}]*\}', '', content_str)
    
    # 2. AGGRESSIVE ESCAPED LINE BREAK CLEANUP 
    content_str = content_str.replace('\\r\\n', ' ').replace('\\r', ' ').replace('\\n', ' ')
    
    content_str = content_str.strip('\'"')
    content_str = content_str.strip()
    
    return content_str

def process_html_content(html_string: str) -> str:
    """
    Processes HTML content with aggressive line break removal and BeautifulSoup parsing.
    """
    if not html_string or html_string is None:
        return ""
    
    try:
        cleaned_content = clean_solution_content(html_string)
        html_str = str(cleaned_content)
        
        # --- FINAL AGGRESSIVE FIX FOR R/N/R/N ERROR ---
        html_str = html_str.replace('\r', '') 
        html_str = re.sub(r'\n+', ' ', html_str)
        html_str = html_str.replace('r/n/r/n', ' ').replace('r/n', ' ') 
        # -----------------------------------------------

        # Use BeautifulSoup to parse and clean the HTML structure
        soup = BeautifulSoup(html_str, 'html.parser')
        
        # Fixing relative image paths
        for img_tag in soup.find_all('img'):
            src = img_tag.get('src')
            if src and src.startswith('//'):
                img_tag['src'] = f"https:{src}"
        
        # Styling for subscripts/superscripts
        for element in soup.find_all(['sub', 'sup']):
            element['style'] = 'font-size: 0.85em; line-height: 1;'
        
        content = str(soup)
        
        # Specific replacements
        content = content.replace('P<sub>s</sub>', '<span style="font-size: 18px;">P<sub style="font-size: 14px;">s</sub></span>')
        content = content.replace('P<sup>0</sup>', '<span style="font-size: 18px;">P<sup style="font-size: 14px;">0</sup></span>')
        
        return content.strip()
    except Exception as e:
        logger.error(f"Error processing HTML content: {e}")
        return str(html_string)

def group_syllabus_topics(question_data_list):
    """
    Analyzes question data to extract and format syllabus topics by subject 
    into a structured dictionary (Physics, Chemistry, Botany, Zoology).
    """
    syllabus_map = {
        "Physics": set(),
        "Chemistry": set(),
        "Botany": set(),
        "Zoology": set(),
    }
    
    for q in question_data_list:
        subject_name = str(q.get("subject_name", "")).strip()
        chapter_name = str(q.get("chapter_name", "")).strip()
        topic_name = str(q.get("topic_name", "")).strip()
        
        subject_key = None
        if "Physics" in subject_name:
            subject_key = "Physics"
        elif "Chemistry" in subject_name:
            subject_key = "Chemistry"
        elif "Botany" in subject_name:
            subject_key = "Botany"
        elif "Zoology" in subject_name:
            subject_key = "Zoology"
            
        if subject_key:
            # Create a combined entry: Chapter - Topic
            topic_entry = chapter_name
            if topic_name and topic_name != chapter_name:
                 topic_entry = f"{chapter_name}: {topic_name}"
            elif not topic_entry:
                 topic_entry = topic_name # In case chapter is missing but topic is present
                 
            if topic_entry:
                syllabus_map[subject_key].add(topic_entry)
                
    return syllabus_map

def generate_syllabus_html_box(metadata_object, question_data_list):
    """
    Generates the HTML box for the syllabus, using the detailed topic grouping 
    from question data if available, or falling back to the raw metadata.
    """
    # 1. Try to generate structured syllabus from question data
    topic_map = group_syllabus_topics(question_data_list)
    has_structured_syllabus = any(topic_map.values())
    
    if has_structured_syllabus:
        html = """
        <div class='syllabus-container'>
            <div class='syllabus-header'>
                <h2> Structured Test Syllabus (Topics Covered by Questions)</h2>
            </div>
            <div class='syllabus-subjects'>
        """
        
        subject_order = ["Physics", "Chemistry", "Botany", "Zoology"]
        
        for subject in subject_order:
            topics = sorted(list(topic_map[subject]))
            if topics:
                html += f"""
                <div class='subject-box'>
                    <h3>{subject}</h3>
                    <ul>
                """
                for topic in topics:
                    cleaned_topic = BeautifulSoup(topic, 'html.parser').get_text().strip()
                    if cleaned_topic:
                        html += f"<li>{cleaned_topic}</li>"
                html += """
                    </ul>
                </div>
                """
                
        html += """
            </div>
        </div>
        """
        return html
    
    # 2. Fallback to raw metadata syllabus/description
    if not metadata_object:
        return ""

    # Prioritize 'syllabus' key, otherwise use 'description'
    syllabus_content_raw = metadata_object.get('syllabus')
    if not syllabus_content_raw or 'no syllabus' in syllabus_content_raw.lower():
        syllabus_content_raw = metadata_object.get('description', '').strip()

    if not syllabus_content_raw:
        return ""
    
    # Process the raw content
    try:
        soup = BeautifulSoup(syllabus_content_raw, 'html.parser')
        # Unwrap <html> and <body> tags if they wrap the content
        for tag in soup.find_all(['html', 'body']):
            tag.unwrap()
        syllabus_body_html = process_html_content(str(soup))
    except Exception as e:
        logger.error(f"Error processing syllabus HTML with BeautifulSoup: {e}")
        syllabus_body_html = process_html_content(syllabus_content_raw) 

    html = f"""
    <div class='syllabus-container raw-metadata'>
        <div class='syllabus-header'>
            <h2> Test Syllabus</h2>
        </div>
        <div class='syllabus-content'>
            {syllabus_body_html}
        </div>
    </div>
    """
    return html

# Theme 1: Questions Only (No answers shown)
def generate_questions_only_html(data, test_title, syllabus_html=""):
    """Generate HTML with only questions and options (no correct answers marked)"""
    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>{test_title}</title>
<style>
    * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }}
    
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #ffffff;
        color: #333333;
        padding: 20px;
        line-height: 1.6;
        max-width: 1200px;
        margin: 0 auto;
        position: relative;
    }}
    
    .header {{
        text-align: center;
        background: linear-gradient(135deg, #e8f5e8, #f0f9f0);
        color: #2d5a2d;
        padding: 30px;
        border-radius: 12px;
        margin-bottom: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        border: 2px solid #c3e6c3;
        position: relative;
        z-index: 2;
    }}
    
    .header h1 {{
        font-size: 32px;
        font-weight: bold;
    }}
    
    /* --- SYLLABUS BOX STYLES (REQUIRED FOR ALL) --- */
    .syllabus-container {{
        border: 2px solid #ced4da;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 30px;
        background-color: #f8f9fa;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        page-break-inside: avoid;
    }}
    
    .syllabus-header h2 {{
        font-size: 24px;
        color: #2196f3;
        border-bottom: 3px solid #2196f3;
        padding-bottom: 10px;
        margin-bottom: 20px;
        text-align: left;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    
    .syllabus-content {{
        display: block;
        width: 100%;
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }}
    
    .subject-line {{
        margin-bottom: 15px;
        font-size: 16px;
        line-height: 1.6;
    }}
    
    .subject-name {{
        font-weight: bold;
        color: #2d5a2d;
        display: inline-block;
        min-width: 80px;
    }}
    
    .subject-topics {{
        color: #424242;
        margin-left: 10px;
    }}
    /* --- END SYLLABUS BOX STYLES --- */
    
    .question-container {{
        background-color: #ffffff;
        border: 2px solid #e9ecef;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 30px;
        page-break-inside: avoid;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        position: relative;
        z-index: 2;
    }}
    
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: linear-gradient(135deg, rgba(173, 216, 230, 0.2), rgba(144, 238, 144, 0.2));
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(102, 205, 170, 0.4);
        backdrop-filter: blur(10px);
        font-size: 14px;
        font-weight: bold;
        color: rgba(72, 139, 139, 0.9);
        z-index: 3;
        pointer-events: auto;
        user-select: none;
        white-space: nowrap;
        letter-spacing: 1px;
        box-shadow: 0 2px 8px rgba(102, 205, 170, 0.2);
        text-decoration: none;
        transition: all 0.3s ease;
    }}
    
    .question-watermark:hover {{
        background: linear-gradient(135deg, rgba(173, 216, 230, 0.35), rgba(144, 238, 144, 0.35));
        border-color: rgba(102, 205, 170, 0.6);
        color: rgba(72, 139, 139, 1);
        transform: scale(1.05);
    }}
    
    .question-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .question-number {{
        background: linear-gradient(135deg, #66cdaa, #48a999);
        color: white;
        min-width: 120px;
        height: 40px;
        border-radius: 20px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 16px;
        box-shadow: 0 2px 8px rgba(102, 205, 170, 0.3);
        padding: 0 15px;
    }}
    
    .question-text {{
        padding: 20px 0;
        margin-bottom: 20px;
        font-size: 18px;
        line-height: 1.7;
        font-weight: 500;
        color: #2d2d2d;
    }}
    
    .options {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 15px;
    }}
    
    .option {{
        background-color: #f8f9fa;
        border: 2px solid #dee2e6;
        border-radius: 8px;
        padding: 15px;
        font-size: 16px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        transition: all 0.2s ease;
    }}
    
    .option-label {{
        background-color: #6c757d;
        color: white;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 14px;
        flex-shrink: 0;
    }}
    
    @media (max-width: 768px) {{
        .options {{
            grid-template-columns: 1fr;
        }}
        
        .syllabus-content {{
            width: 100%;
        }}
        
        body {{
            padding: 15px;
        }}
        
        .question-header {{
            flex-direction: column;
            align-items: flex-start;
        }}
        
        .question-watermark {{
            top: 12px;
            right: 15px;
            padding: 6px 12px;
            font-size: 12px;
            letter-spacing: 0.5px;
        }}
    }}
</style>
</head>
<body>
    <div class='header'>
        <h1>{test_title} - Questions Only</h1>
    </div>
    {syllabus_html}
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-container'>
        <a href='https://t.me/NEETSQUARE' target='_blank' class='question-watermark'>NEETSQUARE</a>
        <div class='question-header'>
            <div class='question-number'>Question {idx}</div>
        </div>
        <div class='question-text'>{processed_body}</div>
        <div class='options'>
        """
        
        # Process options (no correct answer marking)
        alternatives = q["alternatives"][:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels):
                label = labels[opt_idx]
                processed_answer = process_html_content(opt['answer'])
                html += f"""
            <div class='option'>
                <div class='option-label'>{label}</div>
                <div class='option-text'>{processed_answer}</div>
            </div>
                """
        
        html += """
        </div>
    </div>
        """
    
    html += """
</body>
</html>
    """
    
    return html

# Theme 2.1: Questions with Marked Correct Answers (NO solutions)
def generate_questions_answers_only_html(data, test_title, syllabus_html=""):
    """Generate HTML with questions and marked correct answers (NO solutions)"""
    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>{test_title}</title>
<style>
    * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }}
    
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #ffffff;
        color: #333333;
        padding: 20px;
        line-height: 1.6;
        max-width: 1200px;
        margin: 0 auto;
        position: relative;
    }}
    
    .header {{
        text-align: center;
        background: linear-gradient(135deg, #e3f2fd, #bbdefb);
        color: #0d47a1;
        padding: 30px;
        border-radius: 12px;
        margin-bottom: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        border: 2px solid #90caf9;
        position: relative;
        z-index: 2;
    }}
    
    .header h1 {{
        font-size: 32px;
        font-weight: bold;
    }}
    
    /* --- SYLLABUS BOX STYLES (REQUIRED FOR ALL) --- */
    .syllabus-container {{
        border: 2px solid #ced4da;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 30px;
        background-color: #f8f9fa;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        page-break-inside: avoid;
    }}
    
    .syllabus-header h2 {{
        font-size: 24px;
        color: #1976d2;
        border-bottom: 3px solid #1976d2;
        padding-bottom: 10px;
        margin-bottom: 20px;
        text-align: left;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    
    .syllabus-content {{
        display: block;
        width: 100%;
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }}
    
    .subject-line {{
        margin-bottom: 15px;
        font-size: 16px;
        line-height: 1.6;
    }}
    
    .subject-name {{
        font-weight: bold;
        color: #1565c0;
        display: inline-block;
        min-width: 80px;
    }}
    
    .subject-topics {{
        color: #424242;
        margin-left: 10px;
    }}
    /* --- END SYLLABUS BOX STYLES --- */
    
    .question-container {{
        background-color: #ffffff;
        border: 2px solid #e9ecef;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 30px;
        page-break-inside: avoid;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        position: relative;
        z-index: 2;
    }}
    
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: linear-gradient(135deg, rgba(100, 181, 246, 0.2), rgba(66, 165, 245, 0.2));
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(33, 150, 243, 0.4);
        backdrop-filter: blur(10px);
        font-size: 14px;
        font-weight: bold;
        color: rgba(13, 71, 161, 0.9);
        z-index: 3;
        pointer-events: auto;
        user-select: none;
        white-space: nowrap;
        letter-spacing: 1px;
        box-shadow: 0 2px 8px rgba(33, 150, 243, 0.2);
        text-decoration: none;
        transition: all 0.3s ease;
    }}
    
    .question-watermark:hover {{
        background: linear-gradient(135deg, rgba(100, 181, 246, 0.35), rgba(66, 165, 245, 0.35));
        border-color: rgba(33, 150, 243, 0.6);
        color: rgba(13, 71, 161, 1);
        transform: scale(1.05);
    }}
    
    .question-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .question-number {{
        background: linear-gradient(135deg, #42a5f5, #1976d2);
        color: white;
        min-width: 120px;
        height: 40px;
        border-radius: 20px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 16px;
        box-shadow: 0 2px 8px rgba(33, 150, 243, 0.3);
        padding: 0 15px;
    }}
    
    .question-text {{
        padding: 20px 0;
        margin-bottom: 20px;
        font-size: 18px;
        line-height: 1.7;
        font-weight: 500;
        color: #2d2d2d;
    }}
    
    .options {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 15px;
    }}
    
    .option {{
        background-color: #f8f9fa;
        border: 2px solid #dee2e6;
        border-radius: 8px;
        padding: 15px;
        font-size: 16px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        transition: all 0.2s ease;
    }}
    
    .option.correct {{
        background-color: #e3f2fd;
        border-color: #1976d2;
        color: #0d47a1;
        font-weight: 600;
        box-shadow: 0 2px 8px rgba(25,118,210,0.2);
        position: relative;
    }}
    
    .option-label {{
        background-color: #6c757d;
        color: white;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 14px;
        flex-shrink: 0;
    }}
    
    .option.correct .option-label {{
        background-color: #1976d2;
    }}
    
    @media (max-width: 768px) {{
        .options {{
            grid-template-columns: 1fr;
        }}
        
        .syllabus-content {{
            width: 100%;
        }}
        
        body {{
            padding: 15px;
        }}
        
        .question-header {{
            flex-direction: column;
            align-items: flex-start;
        }}
        
        .question-watermark {{
            top: 12px;
            right: 15px;
            padding: 6px 12px;
            font-size: 12px;
            letter-spacing: 0.5px;
        }}
    }}
</style>
</head>
<body>
    <div class='header'>
        <h1>{test_title} - Questions with Answers</h1>
    </div>
    {syllabus_html}
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-container'>
        <a href='https://t.me/SAD_LYFFFF' target='_blank' class='question-watermark'>SAD_LYFFFF</a>
        <div class='question-header'>
            <div class='question-number'>Question {idx}</div>
        </div>
        <div class='question-text'>{processed_body}</div>
        <div class='options'>
        """
        
        # Process options with correct answer marking
        alternatives = q["alternatives"][:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels):
                label = labels[opt_idx]
                is_correct = str(opt.get("score_if_chosen")) == "1"
                opt_class = "option correct" if is_correct else "option"
                processed_answer = process_html_content(opt['answer'])
                html += f"""
            <div class='{opt_class}'>
                <div class='option-label'>{label}</div>
                <div class='option-text'>{processed_answer}</div>
            </div>
                """
        
        html += """
        </div>
    </div>
        """
    
    html += """
</body>
</html>
    """
    
    return html

# Theme 2.2: Questions with Marked Correct Answers (with solutions)
def generate_questions_with_answers_html(data, test_title, syllabus_html=""):
    """Generate HTML with questions, marked correct answers, and solutions"""
    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>{test_title}</title>
<style>
    * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }}
    
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #ffffff;
        color: #333333;
        padding: 20px;
        line-height: 1.6;
        max-width: 1200px;
        margin: 0 auto;
        position: relative;
    }}
    
    .header {{
        text-align: center;
        background: linear-gradient(135deg, #e8f5e8, #f0f9f0);
        color: #2d5a2d;
        padding: 30px;
        border-radius: 12px;
        margin-bottom: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        border: 2px solid #c3e6c3;
        position: relative;
        z-index: 2;
    }}
    
    .header h1 {{
        font-size: 32px;
        font-weight: bold;
    }}
    
    /* --- SYLLABUS BOX STYLES (REQUIRED FOR ALL) --- */
    .syllabus-container {{
        border: 2px solid #ced4da;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 30px;
        background-color: #f8f9fa;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        page-break-inside: avoid;
    }}
    
    .syllabus-header h2 {{
        font-size: 24px;
        color: #2196f3;
        border-bottom: 3px solid #2196f3;
        padding-bottom: 10px;
        margin-bottom: 20px;
        text-align: left;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    
    .syllabus-content {{
        display: block;
        width: 100%;
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }}
    
    .subject-line {{
        margin-bottom: 15px;
        font-size: 16px;
        line-height: 1.6;
    }}
    
    .subject-name {{
        font-weight: bold;
        color: #2d5a2d;
        display: inline-block;
        min-width: 80px;
    }}
    
    .subject-topics {{
        color: #424242;
        margin-left: 10px;
    }}
    /* --- END SYLLABUS BOX STYLES --- */
    
    .question-container {{
        background-color: #ffffff;
        border: 2px solid #e9ecef;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 30px;
        page-break-inside: avoid;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        position: relative;
        z-index: 2;
    }}
    
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: linear-gradient(135deg, rgba(173, 216, 230, 0.2), rgba(144, 238, 144, 0.2));
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(102, 205, 170, 0.4);
        backdrop-filter: blur(10px);
        font-size: 14px;
        font-weight: bold;
        color: rgba(72, 139, 139, 0.9);
        z-index: 3;
        pointer-events: auto;
        user-select: none;
        white-space: nowrap;
        letter-spacing: 1px;
        box-shadow: 0 2px 8px rgba(102, 205, 170, 0.2);
        text-decoration: none;
        transition: all 0.3s ease;
    }}
    
    .question-watermark:hover {{
        background: linear-gradient(135deg, rgba(173, 216, 230, 0.35), rgba(144, 238, 144, 0.35));
        border-color: rgba(102, 205, 170, 0.6);
        color: rgba(72, 139, 139, 1);
        transform: scale(1.05);
    }}
    
    .question-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .question-number {{
        background: linear-gradient(135deg, #66cdaa, #48a999);
        color: white;
        min-width: 120px;
        height: 40px;
        border-radius: 20px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 16px;
        box-shadow: 0 2px 8px rgba(102, 205, 170, 0.3);
        padding: 0 15px;
    }}
    
    .question-text {{
        padding: 20px 0;
        margin-bottom: 20px;
        font-size: 18px;
        line-height: 1.7;
        font-weight: 500;
        color: #2d2d2d;
    }}
    
    .options {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 15px;
    }}
    
    .option {{
        background-color: #f8f9fa;
        border: 2px solid #dee2e6;
        border-radius: 8px;
        padding: 15px;
        font-size: 16px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        transition: all 0.2s ease;
    }}
    
    .option.correct {{
        background-color: #d4edda;
        border-color: #28a745;
        color: #155724;
        font-weight: 600;
        box-shadow: 0 2px 8px rgba(40,167,69,0.2);
        position: relative;
    }}
    
    .option-label {{
        background-color: #6c757d;
        color: white;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 14px;
        flex-shrink: 0;
    }}
    
    .option.correct .option-label {{
        background-color: #28a745;
    }}
    
    .solution-section {{
        background: linear-gradient(135deg, #e3f2fd, #bbdefb);
        border: 2px solid #2196f3;
        border-radius: 12px;
        padding: 20px;
        margin-top: 20px;
    }}
    
    .solution-header {{
        display: flex;
        align-items: center;
        margin-bottom: 15px;
    }}
    
    .solution-icon {{
        background: linear-gradient(135deg, #2196f3, #1976d2);
        color: white;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 16px;
        margin-right: 12px;
    }}
    
    .solution-title {{
        font-size: 18px;
        font-weight: bold;
        color: #1976d2;
    }}
    
    .solution-content {{
        font-size: 16px;
        line-height: 1.7;
        color: #424242;
        background-color: #ffffff;
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }}
    
    .no-solution {{
        color: #757575;
        font-style: italic;
        text-align: center;
        padding: 15px;
        background-color: #fafafa;
        border-radius: 8px;
        border: 1px dashed #e0e0e0;
    }}
    
    @media (max-width: 768px) {{
        .options {{
            grid-template-columns: 1fr;
        }}
        
        .syllabus-content {{
            width: 100%;
        }}
        
        body {{
            padding: 15px;
        }}
        
        .question-header {{
            flex-direction: column;
            align-items: flex-start;
        }}
        
        .question-watermark {{
            top: 12px;
            right: 15px;
            padding: 6px 12px;
            font-size: 12px;
            letter-spacing: 0.5px;
        }}
    }}
</style>
</head>
<body>
    <div class='header'>
        <h1>{test_title}</h1>
    </div>
    {syllabus_html}
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-container'>
        <a href='https://t.me/NEETSQUARE' target='_blank' class='question-watermark'>NEETSQUARE</a>
        <div class='question-header'>
            <div class='question-number'>Question {idx}</div>
        </div>
        <div class='question-text'>{processed_body}</div>
        <div class='options'>
        """
        
        # Process options with correct answer marking
        alternatives = q["alternatives"][:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels):
                label = labels[opt_idx]
                is_correct = str(opt.get("score_if_chosen")) == "1"
                opt_class = "option correct" if is_correct else "option"
                processed_answer = process_html_content(opt['answer'])
                html += f"""
            <div class='{opt_class}'>
                <div class='option-label'>{label}</div>
                <div class='option-text'>{processed_answer}</div>
            </div>
                """
        
        html += """
        </div>
        """
        
        # Add solution section after each question
        detailed_solution = str(q.get("detailed_solution", "")).strip() if q.get("detailed_solution") else ""
        solution = str(q.get("solution", "")).strip() if q.get("solution") else ""
        explanation = str(q.get("explanation", "")).strip() if q.get("explanation") else ""
        
        html += """
        <div class='solution-section'>
            <div class='solution-header'>
                <div class='solution-icon'>💡</div>
                <div class='solution-title'>Solution</div>
            </div>
        """
        
        if detailed_solution:
            html += f"""
            <div class='solution-content'>{process_html_content(detailed_solution)}</div>
            """
        elif solution:
            html += f"""
            <div class='solution-content'>{process_html_content(solution)}</div>
            """
        elif explanation:
            html += f"""
            <div class='solution-content'>{process_html_content(explanation)}</div>
            """
        else:
            html += f"""
            <div class='no-solution'>
                No detailed solution available for this question.
            </div>
            """
        
        html += """
        </div>
    </div>
        """
    
    html += """
</body>
</html>
    """
    
    return html

def format_quiz_info(quiz_data):
    """Format quiz data into readable message"""
    try:
        title = clean_text_for_telegram(quiz_data.get('title', 'N/A'))
        description = clean_text_for_telegram(quiz_data.get('description', 'N/A'))
        syllabus = clean_text_for_telegram(quiz_data.get('syllabus', 'N/A'))
        
        # Extract timing information
        quiz_open = format_timestamp(quiz_data.get('quiz_open'))
        quiz_close = format_timestamp(quiz_data.get('quiz_close'))
        show_results = format_timestamp(quiz_data.get('show_results'))
        
        # Create formatted message using HTML (more reliable than MarkdownV2)
        formatted_message = f"""📋 <b>QUIZ INFORMATION</b>

📚 <b>Test Name:</b>
{title}

📝 <b>Syllabus/Description:</b>
{syllabus if syllabus != 'N/A' else description}

⏰ <b>Timing Information:</b>
🕐 <b>Opens:</b> {quiz_open}
🔒 <b>Closes:</b> {quiz_close}
📊 <b>Results:</b> {show_results}"""
        
        return formatted_message.strip()
    
    except Exception as e:
        logger.error(f"Error formatting quiz data: {e}")
        return "❌ Error formatting quiz data. Please try again."

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /auth command - only owner can use this"""
    user_id = update.effective_user.id
    
    # Check if user is the owner
    if user_id != OWNER_ID:
        if user_id in AUTHORIZED_USER_IDS:
            await update.message.reply_text(
                "🔒 <b>Authorization Access</b>\n\n"
                "ℹ️ You are already an authorized user, but only the owner can authorize new users.\n\n"
                f"👑 Owner ID: <code>{OWNER_ID}</code>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                "🚫 <b>Access Denied</b>\n\n"
                "❌ Only the bot owner can authorize new users.\n\n"
                f"🆔 Your ID: <code>{user_id}</code>\n"
                f"👑 Owner ID: <code>{OWNER_ID}</code>\n\n"
                "💬 Contact the owner for authorization.",
                parse_mode='HTML'
            )
        return ConversationHandler.END
    
    # Owner is trying to authorize someone
    await update.message.reply_text(
        "👑 <b>Owner Authorization Panel</b>\n\n"
        "🔐 You can authorize new users to access this bot.\n\n"
        "📝 Please send the <b>User ID</b> of the person you want to authorize:\n\n"
        "💡 <i>Tip: Users can get their ID by messaging the bot</i>",
        parse_mode='HTML'
    )
    return ASK_AUTH_USER_ID

async def handle_auth_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user ID input for authorization"""
    user_input = update.message.text.strip()
    
    # Validate input
    if not user_input.isdigit():
        await update.message.reply_text(
            "❌ <b>Invalid User ID format!</b>\n\n"
            "📝 Please send a valid numerical User ID.\n\n"
            "💡 <b>Example:</b> 123456789\n\n"
            "🔄 Try again or use /cancel to abort.",
            parse_mode='HTML'
        )
        return ASK_AUTH_USER_ID
    
    new_user_id = int(user_input)
    
    # Check if already authorized
    if new_user_id in AUTHORIZED_USER_IDS:
        await update.message.reply_text(
            "⚠️ <b>Already Authorized</b>\n\n"
            f"✅ User ID <code>{new_user_id}</code> is already in the authorized list.\n\n"
            f"👥 <b>Current authorized users:</b> {len(AUTHORIZED_USER_IDS)} users\n"
            f"📋 <b>IDs:</b> <code>{', '.join(map(str, AUTHORIZED_USER_IDS))}</code>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    
    # Add to authorized users
    AUTHORIZED_USER_IDS.append(new_user_id)
    
    # Create confirmation message
    success_message = (
        "✅ <b>User Successfully Authorized!</b>\n\n"
        f"👤 <b>New User ID:</b> <code>{new_user_id}</code>\n"
        f"📅 <b>Authorized on:</b> {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
        f"👑 <b>Authorized by:</b> Owner\n\n"
        f"👥 <b>Total authorized users:</b> {len(AUTHORIZED_USER_IDS)}\n"
        f"📋 <b>All IDs:</b> <code>{', '.join(map(str, AUTHORIZED_USER_IDS))}</code>\n\n"
        "🎉 The user can now access all bot features!"
    )
    
    await update.message.reply_text(success_message, parse_mode='HTML')
    
    # Log the authorization
    logger.info(f"Owner {OWNER_ID} authorized new user {new_user_id}. Total users: {len(AUTHORIZED_USER_IDS)}")
    
    return ConversationHandler.END

async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /listusers command - only owner can use this"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text(
            "🚫 <b>Access Denied</b>\n\n"
            "❌ Only the bot owner can view the user list.",
            parse_mode='HTML'
        )
        return
    
    # Create user list message
    user_list_message = (
        "👑 <b>Bot Owner Panel - User List</b>\n\n"
        f"👥 <b>Total Authorized Users:</b> {len(AUTHORIZED_USER_IDS)}\n"
        f"👤 <b>Owner ID:</b> <code>{OWNER_ID}</code>\n\n"
        "📋 <b>All Authorized User IDs:</b>\n"
    )
    
    for i, uid in enumerate(AUTHORIZED_USER_IDS, 1):
        status = "👑 (Owner)" if uid == OWNER_ID else "👤 (User)"
        user_list_message += f"{i}. <code>{uid}</code> {status}\n"
    
    user_list_message += (
        f"\n💡 <b>Commands available to owner:</b>\n"
        f"• /auth - Authorize new users\n"
        f"• /listusers - View all authorized users\n"
        f"• /removeuser - Remove user authorization\n"
    )
    
    await update.message.reply_text(user_list_message, parse_mode='HTML')

async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removeuser command - only owner can use this"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text(
            "🚫 <b>Access Denied</b>\n\n"
            "❌ Only the bot owner can remove users.",
            parse_mode='HTML'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Please provide a User ID to remove!</b>\n\n"
            "🔍 <b>Usage:</b> /removeuser &lt;UserID&gt;\n"
            "💡 <b>Example:</b> /removeuser 123456789\n\n"
            "📋 Use /listusers to see all authorized users.",
            parse_mode='HTML'
        )
        return
    
    try:
        target_user_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Invalid User ID format!</b>\n\n"
            "📝 Please provide a valid numerical User ID.\n\n"
            "💡 <b>Example:</b> /removeuser 123456789",
            parse_mode='HTML'
        )
        return
    
    # Check if trying to remove owner
    if target_user_id == OWNER_ID: 
        await update.message.reply_text(
            "🚫 <b>Cannot Remove Owner!</b>\n\n"
            "❌ The owner cannot be removed from the authorized list.\n\n"
            f"👑 Owner ID: <code>{OWNER_ID}</code>",
            parse_mode='HTML'
        )
        return
    
    # Check if user exists in authorized list
    if target_user_id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text(
            "⚠️ <b>User Not Found!</b>\n\n"
            f"❌ User ID <code>{target_user_id}</code> is not in the authorized list.\n\n"
            "📋 Use /listusers to see all authorized users.",
            parse_mode='HTML'
        )
        return
    
    # Remove user
    AUTHORIZED_USER_IDS.remove(target_user_id)
    
    success_message = (
        "✅ <b>User Successfully Removed!</b>\n\n"
        f"👤 <b>Removed User ID:</b> <code>{target_user_id}</code>\n"
        f"📅 <b>Removed on:</b> {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
        f"👑 <b>Removed by:</b> Owner\n\n"
        f"👥 <b>Remaining authorized users:</b> {len(AUTHORIZED_USER_IDS)}\n\n"
        "🔒 This user can no longer access the bot."
    )
    
    await update.message.reply_text(success_message, parse_mode='HTML')
    
    # Log the removal
    logger.info(f"Owner {OWNER_ID} removed user {target_user_id}. Remaining users: {len(AUTHORIZED_USER_IDS)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation with main menu."""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        # NOTE: MarkdownV2 requires escaping all special characters.
        await update.message.reply_text(
            f"🚫 *Access Denied*\n\n❌ You are not authorized to use this bot\\.\n\n🆔 *Your User ID:* `{update.effective_user.id}`",
            parse_mode='MarkdownV2'
        )
        logger.warning(f"Unauthorized access attempt by user ID: {update.effective_user.id}")
        return ConversationHandler.END

    if not check_internet_connection():
        await update.message.reply_text("🌐 *Network connectivity issue detected\\.* Please check your internet connection and try again\\.", parse_mode='MarkdownV2')
        return ConversationHandler.END

    # Create main menu keyboard
    keyboard = [
        [ 
            InlineKeyboardButton("📚 Extract Test", callback_data="extract_test"),
            InlineKeyboardButton("ℹ️ Get Test Info", callback_data="get_info")
        ],
        [ InlineKeyboardButton("❓ Help", callback_data="help") ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # NOTE: MarkdownV2 requires escaping all special characters.
    welcome_message = """🤖 *Test Extraction and Info Bot*
👋 *Welcome\\!* This bot helps you extract and get information about Aakash iTutor tests\\.

✨ *Available Features:*
📚 *Extract Test:* Download test questions in 3 different formats
ℹ️ *Get Test Info:* View detailed test information and syllabus

🚀 *Choose an option below to get started:*"""
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='MarkdownV2')
    return ConversationHandler.END

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu selections"""
    query = update.callback_query
    await query.answer()

    if query.data == "extract_test":
        await query.edit_message_text("📚 *Extract Test*\n\n📢 Please send the *NID* \\(Numerical ID\\) for the test you want to extract:", parse_mode='MarkdownV2')
        return ASK_NID
    elif query.data == "get_info":
        await query.edit_message_text("ℹ️ *Get Test Info*\n\n📢 Please send the *NID* \\(Numerical ID\\) to get test information:", parse_mode='MarkdownV2')
        return ASK_INFO_NID
    elif query.data == "help":
        help_text = """❓ *Help \\- How to use this bot*

📚 *Extract Test:*
• Provide test NID
• Choose from 3 different formats
• Download HTML files with questions/answers and syllabus

ℹ️ *Get Test Info:*
• Provide test NID
• Get detailed test information
• View syllabus and timing details

📋 *Available Extract Formats:*
📝 *Questions Only:* Clean format for practice \\(no answers shown\\)
✅ *Questions \\+ Answers:* Correct answers highlighted
📖 *Questions \\+ Solutions:* Correct answers \\+ detailed solutions after each question

📢 *What is NID?*
NID is the unique identifier for each test on Aakash iTutor platform\\.
💡 *Example:* `4342866055`"""
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
        return ConversationHandler.END # End is fine here, as back_to_menu restarts the flow

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle back to menu button"""
    query = update.callback_query
    await query.answer()
    
    # Re-send the start message
    keyboard = [
        [ 
            InlineKeyboardButton("📚 Extract Test", callback_data="extract_test"),
            InlineKeyboardButton("ℹ️ Get Test Info", callback_data="get_info")
        ],
        [ InlineKeyboardButton("❓ Help", callback_data="help") ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_message = """🤖 *Test Extraction and Info Bot*
✨ *Available Features:*
📚 *Extract Test:* Download test questions in 3 different formats
ℹ️ *Get Test Info:* View detailed test information and syllabus

🚀 *Choose an option below:*"""
    
    await query.edit_message_text(welcome_message, reply_markup=reply_markup, parse_mode='MarkdownV2')
    return ConversationHandler.END # Returning END is safe as we use entry_points in ConversationHandler

async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /extract command"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 *Access Denied*\n\n❌ You are not authorized to use this bot\\.", parse_mode='MarkdownV2')
        return ConversationHandler.END
    
    await update.message.reply_text("📚 *Extract Test*\n\n📢 Please send the *NID* \\(Numerical ID\\) for the test you want to extract:", parse_mode='MarkdownV2')
    return ASK_NID

async def handle_info_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle NID input for info command"""
    nid = update.message.text.strip()
    if not nid.isdigit():
        await update.message.reply_text(
            "❌ <b>Invalid NID format!</b> Please send a numerical ID.\n\n"
            "💡 <b>Example:</b> 4342866055",
            parse_mode='HTML'
        )
        return ASK_INFO_NID

    try:
        loading_message = await update.message.reply_text("🔄 Fetching quiz information... ⏳")
        quiz_data = await fetch_quiz_info(nid)
        
        if quiz_data:
            formatted_info = format_quiz_info(quiz_data)
            await loading_message.edit_text(formatted_info, parse_mode='HTML')
        else:
            await loading_message.edit_text(
                f"❌ <b>No quiz found with NID:</b> <code>{nid}</code>\n\n"
                f"🔍 Please check the NID and try again.",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error in handle_info_nid: {e}")
        try:
            await loading_message.edit_text(
                f"⚠️ <b>An error occurred:</b>\n{str(e)[:200]}\n\n"
                f"🔄 Please try again later.",
                parse_mode='HTML'
            )
        except:
            await update.message.reply_text(
                f"⚠️ An error occurred: {str(e)[:200]}\n\n"
                f"🔄 Please try again later."
            )
            
    return ConversationHandler.END

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /info command with NID parameter"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Access Denied\n\n❌ You are not authorized to use this bot.")
        return

    try:
        if not context.args:
            await update.message.reply_text(
                "⚠️ <b>Please provide an NID!</b>\n\n"
                "🔍 <b>Usage:</b> /info &lt;NID&gt;\n"
                "💡 <b>Example:</b> /info 4342866055",
                parse_mode='HTML'
            )
            return
            
        nid = context.args[0].strip()
        
        if not nid.isdigit():
            await update.message.reply_text(
                "❌ <b>Invalid NID format!</b> NID should be a number.\n\n"
                "💡 <b>Example:</b> /info 4342866055",
                parse_mode='HTML'
            )
            return

        loading_message = await update.message.reply_text("🔄 Fetching quiz information... ⏳")
        quiz_data = fetch_test_metadata(nid) # Use synchronous fetch_test_metadata here
        
        if quiz_data:
            formatted_info = format_quiz_info(quiz_data)
            await loading_message.edit_text(formatted_info, parse_mode='HTML')
        else:
            await loading_message.edit_text(
                f"❌ <b>No quiz found with NID:</b> <code>{nid}</code>\n\n"
                f"🔍 Please check the NID and try again.",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error in info_command: {e}")
        try:
            await update.message.reply_text(
                f"⚠️ <b>An error occurred:</b>\n{str(e)[:200]}\n\n"
                f"🔄 Please try again later.",
                parse_mode='HTML'
            )
        except:
            await update.message.reply_text(
                f"⚠️ An error occurred: {str(e)[:200]}\n\n"
                f"🔄 Please try again later."
            )

async def handle_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the NID from the user for extraction."""
    nid = update.message.text.strip()
    
    if not nid.isdigit():
        await update.message.reply_text("❌ Invalid NID. Please send a numerical ID. 🔢")
        return ASK_NID
        
    context.user_data['nid'] = nid
    
    # Create inline keyboard for format selection (3 options only)
    keyboard = [
        [ 
            InlineKeyboardButton("📝 Questions Only", callback_data="questions_only"),
            InlineKeyboardButton("✅ Questions + Answers", callback_data="questions_answers")
        ],
        [ 
            InlineKeyboardButton("📖 Questions + Solutions", callback_data="questions_solutions")
        ],
        [ 
            InlineKeyboardButton("📦 All Formats (3 files)", callback_data="all_formats")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📋 Choose your preferred format:\n\n"
        "📝 Questions Only: Clean format, no answers shown\n"
        "✅ Questions + Answers: Correct answers highlighted\n"
        "📖 Questions + Solutions: Correct answers + detailed solutions after each question\n"
        "📦 All Formats: Get all 3 files at once",
        reply_markup=reply_markup
    )
    return CHOOSE_FORMAT

async def handle_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the format selection and generates files."""
    query = update.callback_query
    await query.answer()
    
    nid = context.user_data.get('nid')
    if not nid:
        await query.edit_message_text("❌ An error occurred. Please start over with /start.")
        return ConversationHandler.END
        
    format_choice = query.data
    
    # Send initial processing message
    loading_message = await query.edit_message_text("⚙️ Processing your request... This may take a moment due to network conditions... ⏳")

    if not check_internet_connection():
        await query.edit_message_text("🌐 Network connectivity lost. Please check your internet connection and try again. 🔄")
        return ConversationHandler.END
        
    try:
        # 1. Fetch metadata and question data
        test_metadata = fetch_test_metadata(nid)
        data = fetch_locale_json_from_api(nid)
        
        if not data:
            await loading_message.edit_text(
                f"❌ Extraction failed: No questions found for the specified NID <code>{nid}</code>.\n\n"
                "🔍 This could be due to:\n"
                "• Network connectivity issues\n"
                "• Invalid NID or a non-question test\n"
                "• Server temporarily unavailable\n\n"
                "🔄 Please verify the NID and try again later.",
                parse_mode='HTML'
            )
            return ConversationHandler.END

        # 2. Extract title and generate Syllabus HTML (passes both metadata and question data for rich syllabus)
        title = test_metadata.get("title", f"Test {nid}").strip() if test_metadata else f"Test {nid}"
        syllabus_html = generate_syllabus_html_box(test_metadata, data) # Pass metadata and question data

        # 3. Clean title for filename
        clean_title = re.sub(r'[^\w\s\-\.]', '', title).strip().replace(' ', '_')

        # 4. Define formats to generate
        formats_to_generate = []
        if format_choice == "questions_only":
            formats_to_generate.append(("Questions_Only", generate_questions_only_html, "📝"))
        elif format_choice == "questions_answers":
            formats_to_generate.append(("Questions_Answers", generate_questions_answers_only_html, "✅"))
        elif format_choice == "questions_solutions":
            formats_to_generate.append(("Questions_Solutions", generate_questions_with_answers_html, "📖"))
        elif format_choice == "all_formats":
            formats_to_generate.append(("Questions_Only", generate_questions_only_html, "📝"))
            formats_to_generate.append(("Questions_Answers", generate_questions_answers_only_html, "✅"))
            formats_to_generate.append(("Questions_Solutions", generate_questions_with_answers_html, "📖"))
        else:
            await loading_message.edit_text("❌ Invalid format choice. Please start over with /start.")
            return ConversationHandler.END

        # 5. Generate and send files
        sent_files = 0
        for format_name, generator_func, icon in formats_to_generate:
            # Pass the question data, title, and syllabus_html to the generator function
            html_content = generator_func(data, title, syllabus_html) 
            
            # Create a virtual file in memory
            html_file = BytesIO(html_content.encode('utf-8'))
            file_name = f"{clean_title}_{format_name}_{nid}.html"
            html_file.name = file_name
            
            # Send the file
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=html_file,
                caption=f"{icon} Successfully extracted <b>{format_name.replace('_', ' ')}</b> for Test NID <code>{nid}</code>",
                parse_mode='HTML'
            )
            sent_files += 1

        # 6. Final message
        final_message = (
            f"🎉 <b>Extraction Complete!</b>\n\n"
            f"✅ Sent <b>{sent_files}</b> file(s) for test <code>{nid}</code>: <b>{title}</b>.\n\n"
            f"🚀 What would you like to do next? Use /start or the menu button."
        )
        
        await loading_message.edit_text(final_message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error during file generation/sending for NID {nid}: {e}")
        try:
            # If an error occurred, attempt to edit the loading message with the error.
            await loading_message.edit_text(
                f"⚠️ An unexpected error occurred during extraction for NID <code>{nid}</code>: {str(e)[:150]}", 
                parse_mode='HTML'
            )
        except Exception as edit_error:
             logger.error(f"Failed to edit message with error: {edit_error}")


    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text(
        '👋 Operation cancelled. Back to the main menu with /start.',
        parse_mode='MarkdownV2'
    )
    return ConversationHandler.END


def main() -> None:
    """Start the bot."""
    
    # 1. Check for basic connectivity
    if not check_internet_connection():
        logger.error("No internet connection detected. Bot cannot start.")
        print("FATAL ERROR: No internet connection detected. Bot cannot start.")
        return

    # 2. Check Telegram API accessibility
    if not test_telegram_api(BOT_TOKEN):
        logger.error("Telegram API is inaccessible or Bot Token is invalid.")
        print("FATAL ERROR: Telegram API is inaccessible or Bot Token is invalid.")
        return

    logger.info("Starting Telegram Bot Application...")

    # Create the Application and pass it your bot's token.
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation Handler for main menu, extraction, and authorization
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("extract", extract_command),
            CommandHandler("auth", auth_command),
        ],
        states={
            ASK_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_nid)],
            CHOOSE_FORMAT: [CallbackQueryHandler(handle_format_choice)],
            ASK_INFO_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_info_nid)],
            ASK_AUTH_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auth_user_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command), MessageHandler(filters.COMMAND, start)],
    )

    application.add_handler(conv_handler)
    
    # Other handlers (non-conversation)
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("listusers", list_users_command))
    application.add_handler(CommandHandler("removeuser", remove_user_command))
    
    # Callback handlers for menu navigation (outside of conversation states)
    application.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$"))
    # Handle the main menu buttons when clicked outside the conversation flow (if user exits via /cancel)
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^extract_test$|^get_info$|^help$"))
    
    # Run the bot until the user presses Ctrl-C
    try:
        application.run_polling(poll_interval=1, allowed_updates=Update.ALL_TYPES)
    except Conflict:
        logger.error("Conflict error: Multiple bots running with the same token.")
        print("FATAL ERROR: Conflict error, bot already running or multiple instances.")
    except NetworkError as e:
        logger.error(f"Network error during polling: {e}")
        print(f"FATAL ERROR: Network error: {e}")
    except TimedOut:
        logger.warning("Polling timed out. Restarting polling.")
        # Attempt to restart the bot on timeout
        main() 
    except Exception as e:
        logger.critical(f"Unhandled critical error: {e}")
        print(f"FATAL ERROR: Unhandled critical error: {e}")


if __name__ == "__main__":
    main()
