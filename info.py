
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
ASK_NID, ASK_PDF_NAME, CHOOSE_FORMAT, ASK_INFO_NID = range(4)

# IMPORTANT: Replace with your actual bot token and authorized user IDs
BOT_TOKEN = "7569082224:AAHKhpg_MfaMbdXtLYiD2nVlVWXPN9kz4JU"
AUTHORIZED_USER_IDS = [7927314662, 8188515782, 7686927258]

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
    """Clean text to prevent Telegram parsing errors"""
    if not text:
        return "N/A"
    
    # Convert to string and basic cleaning
    text = str(text).strip()
    
    # Remove HTML tags
    text = re.sub(r'<[^>]*>', '', text)
    
    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z0-9#]+;', '', text)
    
    # Remove problematic characters that can cause parsing issues
    text = re.sub(r'[^\w\s\-\.\(\),:\n/]', '', text)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n+', '\n', text).strip()
    
    # Limit length to prevent issues
    if len(text) > 800:
        text = text[:797] + "..."
    
    return text if text else "N/A"

def fetch_syllabus_data(nid: str):
    """Fetches syllabus data from the API for a given NID with enhanced error handling."""
    url = f"https://learn.aakashitutor.com/get/test/syllabus?nid={nid}"
    
    retry_configs = [
        {"timeout": 15, "headers": {}},
        {"timeout": 30, "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}},
        {"timeout": 45, "headers": {"User-Agent": "Python-requests/2.28.0"}},
    ]
    
    for i, config in enumerate(retry_configs):
        try:
            logger.info(f"Attempt {i+1} to fetch syllabus for NID {nid}")
            response = requests.get(url, timeout=config["timeout"], headers=config["headers"])
            response.raise_for_status()
            syllabus_data = response.json()
            logger.info(f"Successfully fetched syllabus for NID {nid} on attempt {i+1}")
            return syllabus_data
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error on attempt {i+1} for syllabus NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout on attempt {i+1} for syllabus NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error on attempt {i+1} for syllabus NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error on attempt {i+1} for syllabus NID {nid}: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error on attempt {i+1} for syllabus NID {nid}: {e}")
            if i < len(retry_configs) - 1:
                time.sleep(2 ** i)

    logger.error(f"All attempts failed for syllabus NID {nid}")
    return None

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
                            languages = english_version.get("language", [])
                            language_names = english_version.get("language_names", [])
                            
                            if "843" in languages or "English" in language_names:
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
                                    "subject_name": english_version.get("subject_name", ""),
                                    "topic": english_version.get("topic", ""),
                                    "topic_name": english_version.get("topic_name", ""),
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

def fetch_test_title_and_description(nid: str):
    """Fetches the test title and description with enhanced error handling."""
    url = f"https://learn.aakashitutor.com/api/getquizfromid?nid={nid}"
    
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                item = data[0]
                title = item.get("title", f"Test {nid}").strip()
                description = item.get("description", "").strip()
                return title, description
            return f"Test {nid}", ""
        except Exception as e:
            logger.error(f"Error fetching title on attempt {attempt+1} for NID {nid}: {e}")
            if attempt < 2:
                time.sleep(2)
    
    return f"Test {nid}", ""

async def fetch_quiz_info(nid):
    """Fetch quiz information for info command"""
    try:
        url = f"https://learn.aakashitutor.com/api/getquizfromid?nid={nid}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        return data[0]
        return None
    
    except Exception as e:
        logger.error(f"Error fetching quiz info: {e}")
        return None

def format_quiz_info(quiz_data):
    """Format quiz data into readable message"""
    try:
        title = clean_text_for_telegram(quiz_data.get('title', 'N/A'))
        description = clean_text_for_telegram(quiz_data.get('description', 'N/A'))
        
        # Extract timing information
        quiz_open = format_timestamp(quiz_data.get('quiz_open'))
        quiz_close = format_timestamp(quiz_data.get('quiz_close'))
        show_results = format_timestamp(quiz_data.get('show_results'))
        
        # Escape special characters for MarkdownV2
        title = title.replace('.', '\.').replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('!', '\!')
        description = description.replace('.', '\.').replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('!', '\!')
        quiz_open = quiz_open.replace('.', '\.').replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('!', '\!').replace(':', '\:')
        quiz_close = quiz_close.replace('.', '\.').replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('!', '\!').replace(':', '\:')
        show_results = show_results.replace('.', '\.').replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('!', '\!').replace(':', '\:')
        
        # Create formatted message with emojis and bold text
        formatted_message = f"""📋 *QUIZ INFORMATION*

📚 *Test Name:*
{title}

📝 *Description and Syllabus:*
{description}

⏰ *Timing Information:*
🕒 *Opens:* {quiz_open}
🔒 *Closes:* {quiz_close}
📊 *Results:* {show_results}"""
        
        return formatted_message.strip()
    
    except Exception as e:
        logger.error(f"Error formatting quiz data: {e}")
        return "❌ Error formatting quiz data\. Please try again\."

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
    """Cleans solution content by removing NID numbers and JSON-like artifacts."""
    if not content or content is None:
        return ""
    
    content_str = str(content)
    
    content_str = re.sub(r'\{[\'"]nid[\'"]:\s*[\'"][0-9]+[\'"],\s*[\'"]content[\'"]:\s*[\'"]', '', content_str)
    content_str = re.sub(r',\s*[\'"]clipping_nid[\'"]:\s*None,\s*[\'"]type[\'"]:\s*[\'"]HTML5[\'"],\s*[\'"]duration[\'"]:\s*None\}.*?$', '', content_str)
    content_str = re.sub(r'\{[^}]*\}', '', content_str)
    content_str = content_str.strip('\'"')
    content_str = content_str.strip()
    
    return content_str

def process_html_content(html_string: str) -> str:
    """Processes HTML content with better error handling."""
    if not html_string or html_string is None:
        return ""
    
    try:
        cleaned_content = clean_solution_content(html_string)
        html_str = str(cleaned_content)
        
        soup = BeautifulSoup(html_str, 'html.parser')
        
        for img_tag in soup.find_all('img'):
            src = img_tag.get('src')
            if src and src.startswith('//'):
                img_tag['src'] = f"https:{src}"
        
        for element in soup.find_all(['sub', 'sup']):
            element['style'] = 'font-size: 0.85em; line-height: 1;'
        
        content = str(soup)
        content = content.replace('P<sub>s</sub>', '<span style="font-size: 18px;">P<sub style="font-size: 14px;">s</sub></span>')
        content = content.replace('P<sup>0</sup>', '<span style="font-size: 18px;">P<sup style="font-size: 14px;">0</sup></span>')
        
        return content
    except Exception as e:
        logger.error(f"Error processing HTML content: {e}")
        return str(html_string)

def format_syllabus_content(syllabus_data):
    """Formats syllabus data into simple, clean format."""
    if not syllabus_data:
        return "<p>No syllabus data available.</p>"
    
    try:
        subjects_data = {
            'Physics': "",
            'Chemistry': "",
            'Botany': "",
            'Zoology': ""
        }
        
        def clean_and_join_topics(topics_text):
            if not topics_text:
                return ""
            
            text_str = str(topics_text).strip()
            text_str = re.sub(r'<[^>]*>', '', text_str)
            text_str = re.sub(r'\\r\\n|\\n|\\r', ' ', text_str)
            text_str = re.sub(r'\s+', ' ', text_str)
            text_str = text_str.replace('\\', '').replace('"', '').replace("'", "")
            
            unwanted_terms = ['test', 'quiz', 'exam', 'nid', 'title', 'description']
            for term in unwanted_terms:
                text_str = re.sub(rf'\b{term}\b', '', text_str, flags=re.IGNORECASE)
            
            text_str = re.sub(r'\s*,\s*', ', ', text_str)
            text_str = re.sub(r',+', ',', text_str)
            text_str = re.sub(r'^[,\s]+|[,\s]+$', '', text_str)
            
            words = text_str.split()
            cleaned_words = []
            for word in words:
                if word in [',', '.', ';', ':']:
                    cleaned_words.append(word)
                elif len(word) <= 3 and word.isupper():
                    cleaned_words.append(word)
                else:
                    if word and word[0].isalpha():
                        cleaned_words.append(word[0].upper() + word[1:].lower())
                    else:
                        cleaned_words.append(word)
            
            result = ' '.join(cleaned_words)
            result = re.sub(r'\s+', ' ', result).strip()
            
            return result
        
        def extract_from_quiz_desc(description):
            if not description:
                return {}
            
            desc_clean = str(description).replace('\\r\\n', ' ').replace('\\n', ' ')
            
            patterns = {
                'Physics': r'Physics\s*[:\uff1a]\s*(.*?)(?=\s*(?:Chemistry|Botany|Zoology|$))',
                'Chemistry': r'Chemistry\s*[:\uff1a]\s*(.*?)(?=\s*(?:Physics|Botany|Zoology|$))',
                'Botany': r'Botany\s*[:\uff1a]\s*(.*?)(?=\s*(?:Physics|Chemistry|Zoology|$))',
                'Zoology': r'Zoology\s*[:\uff1a]\s*(.*?)(?=\s*(?:Physics|Chemistry|Botany|$))'
            }
            
            extracted = {}
            for subject, pattern in patterns.items():
                match = re.search(pattern, desc_clean, re.IGNORECASE | re.DOTALL)
                if match:
                    topics_text = match.group(1).strip()
                    cleaned_topics = clean_and_join_topics(topics_text)
                    if cleaned_topics and len(cleaned_topics) > 3:
                        extracted[subject] = cleaned_topics
            
            return extracted
        
        if isinstance(syllabus_data, list):
            for item in syllabus_data:
                if isinstance(item, dict):
                    if 'quiz_desc' in item:
                        quiz_topics = extract_from_quiz_desc(item['quiz_desc'])
                        for subject, topics in quiz_topics.items():
                            if not subjects_data[subject]:
                                subjects_data[subject] = topics
                    elif 'description' in item:
                        quiz_topics = extract_from_quiz_desc(item['description'])
                        for subject, topics in quiz_topics.items():
                            if not subjects_data[subject]:
                                subjects_data[subject] = topics
        
        elif isinstance(syllabus_data, dict):
            if 'quiz_desc' in syllabus_data:
                quiz_topics = extract_from_quiz_desc(syllabus_data['quiz_desc'])
                for subject, topics in quiz_topics.items():
                    if not subjects_data[subject]:
                        subjects_data[subject] = topics
            elif 'description' in syllabus_data:
                quiz_topics = extract_from_quiz_desc(syllabus_data['description'])
                for subject, topics in quiz_topics.items():
                    if not subjects_data[subject]:
                        subjects_data[subject] = topics
            
            for key, value in syllabus_data.items():
                if isinstance(value, str) and len(value) > 50:
                    if any(subj.lower() in value.lower() for subj in ['physics', 'chemistry', 'botany', 'zoology']):
                        quiz_topics = extract_from_quiz_desc(value)
                        for subject, topics in quiz_topics.items():
                            if not subjects_data[subject]:
                                subjects_data[subject] = topics
        
        topics_lines = []
        subject_order = ['Physics', 'Chemistry', 'Botany', 'Zoology']
        
        for subject in subject_order:
            topics_text = subjects_data[subject]
            if topics_text and topics_text.strip():
                if not topics_text.endswith('.'):
                    topics_text += '.'
                line = f"<strong>{subject}:</strong> {topics_text}"
                topics_lines.append(line)
        
        if not topics_lines:
            return "<p class='no-syllabus'>No structured syllabus data found for Physics, Chemistry, Botany, or Zoology.</p>"
        
        syllabus_html = "<div class='topics-covered'>"
        for line in topics_lines:
            syllabus_html += f"<p class='subject-line'>{line}</p>"
        syllabus_html += "</div>"
        
        return syllabus_html
        
    except Exception as e:
        logger.error(f"Error formatting syllabus content: {e}")
        return f"<p class='error-message'>Error processing syllabus data: {str(e)}</p>"

# Theme 1: NEET Style Question Paper with Syllabus (2-column printable format)
# Theme 1: NEET Style Question Paper with Syllabus (2-column printable format)
def generate_neet_style_paper_with_syllabus(data, test_title, syllabus_data):
    """Generate NEET-style question paper with syllabus - printable 2-column format with no answers marked"""
    
    # Format syllabus content
    syllabus_content = format_syllabus_content(syllabus_data)
    
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
    
    @page {{
        size: A4;
        margin: 15mm 10mm;
    }}
    
    body {{
        font-family: 'Times New Roman', serif;
        font-size: 17px;
        line-height: 1.3;
        color: #000;
        background: white;
        position: relative;
    }}
    
    .header {{
        text-align: center;
        border: 2px solid #000;
        padding: 10px;
        margin-bottom: 12px;
        background: #f9f9f9;
        position: relative;
    }}
    
    .watermark-box {{
        position: absolute;
        top: 6px;
        right: 6px;
        background: rgba(173, 216, 230, 0.3);
        border: 1px solid #87CEEB;
        padding: 3px 6px;
        font-size: 9px;
        font-weight: bold;
        color: #4682B4;
        border-radius: 3px;
        user-select: none;
        -webkit-user-select: none;
        -moz-user-select: none;
        -ms-user-select: none;
        pointer-events: none;
        -webkit-touch-callout: none;
        -webkit-user-drag: none;
    }}
    
    .watermark-box a {{
        color: #4682B4;
        text-decoration: none;
        font-weight: bold;
    }}
    
    .watermark-box a:hover {{
        text-decoration: underline;
    }}
    
    .header h1 {{
        font-size: 23px;
        font-weight: bold;
        margin-bottom: 4px;
    }}
    
    .header .details {{
        display: flex;
        justify-content: space-between;
        margin-top: 5px;
        font-size: 17px;
        font-weight: bold;
    }}
    
    .syllabus-section {{
        border: 2px solid #000;
        padding: 10px;
        margin-bottom: 12px;
        background: #f5f5f5;
        page-break-inside: avoid;
        border-radius: 5px;
    }}
    
    .syllabus-section h3 {{
        font-family: 'Times New Roman', serif;
        font-size: 19px;
        font-weight: bold;
        margin-bottom: 6px;
        text-align: left;
        color: #000;
        text-transform: uppercase;
        border-bottom: 1px solid #000;
        padding-bottom: 2px;
    }}
    
    .topics-covered {{
        font-family: 'Times New Roman', serif;
        font-size: 16px;
        line-height: 1.4;
        color: #000;
        text-align: justify;
    }}
    
    .subject-line {{
        margin-bottom: 4px;
        text-align: justify;
        line-height: 1.4;
        font-weight: normal;
    }}
    
    .subject-line strong {{
        font-weight: bold;
        color: #000;
    }}
    
    .no-syllabus {{
        font-family: 'Times New Roman', serif;
        font-size: 13px;
        color: #666;
        text-align: center;
        font-style: italic;
        padding: 6px;
    }}
    
    .error-message {{
        font-family: 'Times New Roman', serif;
        font-size: 13px;
        color: #cc0000;
        text-align: center;
        padding: 6px;
    }}
    
    .instructions {{
        border: 1px solid #000;
        padding: 8px;
        margin-bottom: 10px;
        background: #f5f5f5;
        font-size: 15px;
    }}
    
    .instructions h3 {{
        font-size: 16px;
        margin-bottom: 4px;
        text-decoration: underline;
    }}
    
    .instructions ul {{
        margin-left: 15px;
    }}
    
    .instructions li {{
        margin-bottom: 2px;
        line-height: 1.4;
    }}
    
    .subject-header {{
        background: linear-gradient(135deg, #2c3e50, #34495e);
        color: white;
        text-align: center;
        padding: 6px 12px;
        font-weight: bold;
        font-size: 17px;
        margin: 10px 0 8px 0;
        border-radius: 6px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
        border: 1px solid #34495e;
        page-break-after: avoid;
        position: relative;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    
    .subject-header::before {{
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: linear-gradient(45deg, transparent 30%, rgba(255,255,255,0.1) 50%, transparent 70%);
        border-radius: 6px;
        pointer-events: none;
    }}
    
    .questions-container {{
        column-count: 2;
        column-gap: 10mm;
        column-rule: 1px solid #ddd;
        position: relative;
    }}
    
    .question {{
        break-inside: avoid;
        margin-bottom: 6px;
        padding: 5px;
        border: 1px solid #ddd;
        background: white;
        position: relative;
    }}
    
    .question-number {{
        font-weight: bold;
        font-size: 16px;
        margin-bottom: 3px;
        color: #000;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    
    .question-watermark {{
        background: rgba(173, 216, 230, 0.3);
        border: 1px solid #87CEEB;
        padding: 1px 4px;
        font-size: 7px;
        color: #4682B4;
        border-radius: 2px;
        user-select: none;
        -webkit-user-select: none;
        -moz-user-select: none;
        -ms-user-select: none;
        pointer-events: none;
        font-family: Arial, sans-serif;
    }}
    
    .question-watermark a {{
        color: #4682B4;
        text-decoration: none;
        font-size: 7px;
    }}
    
    .question-text {{
        margin-bottom: 4px;
        font-size: 15px;
        line-height: 1.3;
        font-weight: 500;
    }}
    
    .options {{
        margin-left: 5px;
    }}
    
    .option {{
        margin-bottom: 2px;
        font-size: 15px;
        line-height: 1.3;
        display: flex;
        align-items: flex-start;
        gap: 3px;
    }}
    
    .option-label {{
        font-weight: bold;
        min-width: 20px;
    }}
    
    .option-text {{
        flex: 1;
    }}
    
    .footer {{
        margin-top: 12px;
        text-align: center;
        font-size: 10px;
        border-top: 1px solid #000;
        padding-top: 6px;
    }}
    
    /* Print specific styles */
    @media print {{
        .header {{
            background: white !important;
        }}
        
        .instructions {{
            background: white !important;
        }}
        
        .syllabus-section {{
            background: white !important;
        }}
        
        .question {{
            background: white !important;
        }}
        
        .subject-header {{
            background: #2c3e50 !important;
            -webkit-print-color-adjust: exact;
            color-adjust: exact;
        }}
        
        body::before,
        .watermark-overlay,
        .watermark-overlay::before,
        .watermark-box,
        .question-watermark {{
            -webkit-print-color-adjust: exact !important;
            color-adjust: exact !important;
            print-color-adjust: exact !important;
        }}
    }}
</style>
</head>
<body>
    <div class='watermark-overlay'></div>
    
    <div class='header'>
        <div class='watermark-box'><a href='https://t.me/SAD_LYFFFF' target='_blank'>SAD_LYFFFF</a></div>
        <h1>{test_title}</h1>
        <div class='details'>
            <span>Time: 3 Hours</span>
            <span>Maximum Marks: 720</span>
            <span>Total Questions: 180</span>
        </div>
    </div>
    
    <div class='syllabus-section'>
        <h3>Syllabus</h3>
        {syllabus_content}
    </div>
    
    <div class='instructions'>
        <h3>GENERAL INSTRUCTIONS:</h3>
        <ul>
            <li>This question paper contains 180 multiple choice questions (MCQs).</li>
            <li>Each question carries 4 marks for correct answer and -1 mark for wrong answer.</li>
            <li>Use only Black/Blue Ball Point Pen for marking answers in OMR sheet.</li>
            <li>Each question has four alternatives. Choose the most appropriate alternative.</li>
            <li>Rough work should be done only in the space provided for rough work in the test booklet.</li>
        </ul>
    </div>
    """
    
    # Define sections with question ranges
    sections = [
        {"name": "PHYSICS", "start": 1, "end": 45},
        {"name": "CHEMISTRY", "start": 46, "end": 90},
        {"name": "BOTANY", "start": 91, "end": 135},
        {"name": "ZOOLOGY", "start": 136, "end": 180}
    ]
    
    question_counter = 1
    
    # Generate questions section by section
    for section in sections:
        html += f"""
    <div style='text-align: center;'>
        <div class='subject-header'>
            {section['name']}
        </div>
    </div>
    
    <div class='questions-container'>
        """
        
        # Calculate how many questions to take for this section
        section_size = section['end'] - section['start'] + 1
        section_questions = data[(question_counter-1):(question_counter-1+section_size)]
        
        for q in section_questions:
            if question_counter > 180:  # Safety check
                break
                
            processed_body = process_html_content(q['body'])
            
            html += f"""
        <div class='question'>
            <div class='question-number'>
                <span>Q.{question_counter}</span>
                <span class='question-watermark'><a href='https://t.me/SAD_LYFFFF' target='_blank'>SAD_LYFFFF</a></span>
            </div>
            <div class='question-text'>{processed_body}</div>
            <div class='options'>
            """
            
            # Process options without marking correct answers
            alternatives = q["alternatives"][:4]
            labels = ["(1)", "(2)", "(3)", "(4)"]
            
            for opt_idx, opt in enumerate(alternatives):
                if opt_idx < len(labels):
                    label = labels[opt_idx]
                    processed_answer = process_html_content(opt['answer'])
                    html += f"""
                <div class='option'>
                    <span class='option-label'>{label}</span>
                    <span class='option-text'>{processed_answer}</span>
                </div>
                    """
            
            html += """
            </div>
        </div>
            """
            question_counter += 1
        
        html += """
    </div>
        """
    
    html += """
    <div class='footer'>
    </div>
</body>
</html>
    """
    
    return html
    
# Theme 2: Modern Style Questions Only - MODIFIED FOR PDF
def generate_questions_only_html(data, test_title):
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
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 30px;
        border-radius: 12px;
        margin-bottom: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        position: relative;
        z-index: 2;
    }}
    
    .header h1 {{
        font-size: 32px;
        font-weight: bold;
    }}
    
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
    
    /* Question watermark - top right of each question */
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: rgba(102, 126, 234, 0.1);
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(102, 126, 234, 0.2);
        backdrop-filter: blur(10px);
        font-size: 14px;
        font-weight: bold;
        color: rgba(102, 126, 234, 0.8);
        z-index: 3;
        pointer-events: auto;
        user-select: none;
        white-space: nowrap;
        letter-spacing: 1px;
        box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);
        text-decoration: none;
        transition: all 0.3s ease;
    }}
    
    .question-watermark:hover {{
        background: rgba(102, 126, 234, 0.2);
        border-color: rgba(102, 126, 234, 0.4);
        color: rgba(102, 126, 234, 1);
        transform: scale(1.05);
    }}
    
    .question-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .question-number {{
        background: linear-gradient(135deg, #007bff, #0056b3);
        color: white;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 18px;
        box-shadow: 0 2px 8px rgba(0,123,255,0.3);
    }}
    
    .question-text {{
        background-color: #f8f9fa;
        border-left: 4px solid #007bff;
        border-radius: 6px;
        padding: 20px;
        margin-bottom: 20px;
        font-size: 16px;
        line-height: 1.7;
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
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-container'>
        <a href='https://t.me/SAD_LYFFFF' target='_blank' class='question-watermark'>SAD_LYFFFF</a>
        <div class='question-header'>
            <div class='question-number'>{idx}</div>
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

# Theme 3: Questions with Marked Correct Answers - MODIFIED FOR PDF
def generate_questions_with_answers_html(data, test_title):
    """Generate HTML with questions and marked correct answers (no solutions)"""
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
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 30px;
        border-radius: 12px;
        margin-bottom: 30px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        position: relative;
        z-index: 2;
    }}
    
    .header h1 {{
        font-size: 32px;
        font-weight: bold;
    }}
    
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
    
    /* Question watermark - top right of each question */
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: rgba(102, 126, 234, 0.1);
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(102, 126, 234, 0.2);
        backdrop-filter: blur(10px);
        font-size: 14px;
        font-weight: bold;
        color: rgba(102, 126, 234, 0.8);
        z-index: 3;
        pointer-events: none;
        user-select: none;
        white-space: nowrap;
        letter-spacing: 1px;
        box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);
    }}
    
    .question-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .question-number {{
        background: linear-gradient(135deg, #007bff, #0056b3);
        color: white;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 18px;
        box-shadow: 0 2px 8px rgba(0,123,255,0.3);
    }}
    
    .question-text {{
        background-color: #f8f9fa;
        border-left: 4px solid #007bff;
        border-radius: 6px;
        padding: 20px;
        margin-bottom: 20px;
        font-size: 16px;
        line-height: 1.7;
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
    
    .option.correct::after {{
        content: "✓ CORRECT";
        position: absolute;
        top: -8px;
        right: 10px;
        background: #28a745;
        color: white;
        font-size: 10px;
        font-weight: bold;
        padding: 2px 8px;
        border-radius: 10px;
        letter-spacing: 0.5px;
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
    
    @media (max-width: 768px) {{
        .options {{
            grid-template-columns: 1fr;
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
    """
    
    for idx, q in enumerate(data, 1):
        processed_body = process_html_content(q['body'])
        
        html += f"""
    <div class='question-container'>
        <div class='question-watermark'>SAD_LYFFFF</div>
        <div class='question-header'>
            <div class='question-number'>{idx}</div>
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

# Theme 4: Complete Solutions with Answer Key
def generate_solutions_html(data, test_title):
    """Generate HTML with answer key table and detailed solutions"""
    answer_key_rows = ""
    for idx, q in enumerate(data, 1):
        correct_option_label = "N/A"
        correct_answer_text = "Not available"
        
        alternatives = q.get("alternatives", [])[:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels) and str(opt.get("score_if_chosen")) == "1":
                correct_option_label = labels[opt_idx]
                correct_answer_text = process_html_content(opt.get('answer'))
                break
        
        answer_key_rows += f"""
        <tr>
            <td class='table-question-number'>{idx}</td>
            <td class='table-correct-option'>{correct_option_label}</td>
            <td class='table-answer-text'>{correct_answer_text}</td>
        </tr>
        """
    
    html_template = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>{test_title} - Solutions</title>
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
    }}
    
    .answer-key-section {{
        background-color: #ffffff;
        border: 3px solid #28a745;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 40px;
        box-shadow: 0 4px 15px rgba(40,167,69,0.1);
        page-break-after: always;
    }}
    
    .answer-key-title {{
        text-align: center;
        background: linear-gradient(135deg, #17a2b8, #138496);
        color: white;
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 25px;
        font-size: 24px;
        font-weight: bold;
        letter-spacing: 1px;
    }}
    
    .test-title-large {{
        text-align: center;
        margin-bottom: 20px;
        font-size: 28px;
        color: #17a2b8;
        font-weight: bold;
        letter-spacing: 0.5px;
        padding: 10px 0;
        border-bottom: 2px solid #17a2b8;
        margin-bottom: 30px;
    }}
    
    .answer-key-table {{
        width: 100%;
        border-collapse: collapse;
        margin: 0 auto;
        background-color: #ffffff;
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        border: 1px solid #dee2e6;
    }}
    
    .answer-key-table th {{
        background: linear-gradient(135deg, #17a2b8, #138496);
        color: white;
        padding: 15px 12px;
        text-align: center;
        font-weight: bold;
        font-size: 16px;
        border: 1px solid #138496;
        letter-spacing: 0.5px;
    }}
    
    .answer-key-table td {{
        padding: 12px;
        text-align: center;
        border: 1px solid #dee2e6;
        font-size: 15px;
        vertical-align: middle;
    }}
    
    .answer-key-table tr:nth-child(even) {{
        background-color: #f8f9fa;
    }}
    
    .answer-key-table tr:nth-child(odd) {{
        background-color: #ffffff;
    }}
    
    .answer-key-table tr:hover {{
        background-color: #e3f2fd;
        transition: all 0.2s ease;
    }}
    
    .table-question-number {{
        font-weight: bold;
        color: #495057;
        font-size: 16px;
        width: 15%;
    }}
    
    .table-correct-option {{
        font-weight: bold;
        color: #17a2b8;
        font-size: 18px;
        width: 15%;
        background-color: #e1f7fa !important;
    }}
    
    .table-answer-text {{
        text-align: left;
        padding-left: 15px;
        line-height: 1.4;
        width: 70%;
        color: #495057;
    }}
    
    .detailed-solutions-section {{
        page-break-before: always;
    }}
    
    .detailed-solutions-title {{
        text-align: center;
        background: linear-gradient(135deg, #6f42c1, #563d7c);
        color: white;
        padding: 20px;
        border-radius: 12px;
        margin-bottom: 30px;
        font-size: 28px;
        font-weight: bold;
        letter-spacing: 1px;
        box-shadow: 0 4px 15px rgba(111,66,193,0.2);
    }}
    
    .solution-container {{
        background-color: #ffffff;
        border: 2px solid #e9ecef;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 30px;
        page-break-inside: avoid;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        position: relative;
    }}
    
    .question-watermark {{
        position: absolute;
        top: 15px;
        right: 20px;
        background: rgba(102, 126, 234, 0.1);
        padding: 8px 16px;
        border-radius: 20px;
        border: 2px solid rgba(102, 126, 234, 0.2);
        font-size: 14px;
        font-weight: bold;
        color: rgba(102, 126, 234, 0.8);
        text-decoration: none;
        transition: all 0.3s ease;
    }}
    
    .question-watermark:hover {{
        background: rgba(102, 126, 234, 0.2);
        border-color: rgba(102, 126, 234, 0.4);
        color: rgba(102, 126, 234, 1);
        transform: scale(1.05);
    }}
    
    .solution-header {{
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }}
    
    .solution-number {{
        background: linear-gradient(135deg, #28a745, #20c997);
        color: white;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 18px;
        box-shadow: 0 2px 8px rgba(40,167,69,0.3);
    }}
    
    .correct-answer-inline {{
        background-color: #f8f9fa;
        border-left: 4px solid #28a745;
        border-radius: 6px;
        padding: 15px 20px;
        margin-bottom: 25px;
        font-size: 16px;
        line-height: 1.6;
    }}
    
    .correct-label {{
        font-weight: bold;
        color: #28a745;
        font-size: 18px;
        display: inline;
    }}
    
    .answer-text-inline {{
        color: #155724;
        font-weight: 600;
        display: inline;
        margin-left: 5px;
    }}
    
    .solution-section {{
        background-color: #f8f9fa;
        border-left: 4px solid #007bff;
        border-radius: 6px;
        padding: 25px;
        margin-bottom: 20px;
    }}
    
    .solution-label {{
        display: flex;
        align-items: center;
        font-size: 18px;
        font-weight: bold;
        color: #007bff;
        margin-bottom: 15px;
    }}
    
    .solution-content {{
        font-size: 16px;
        line-height: 1.7;
        color: #495057;
    }}
    
    .no-solution {{
        color: #6c757d;
        font-style: italic;
        padding: 15px;
        text-align: center;
        background-color: #f8f9fa;
        border-radius: 6px;
        border: 1px dashed #dee2e6;
    }}
    
    @media print {{
        .answer-key-section {{
            page-break-after: always;
        }}
        
        .detailed-solutions-section {{
            page-break-before: always;
        }}
        
        .solution-container {{
            page-break-inside: avoid;
        }}
    }}
    
    @media (max-width: 768px) {{
        body {{
            padding: 15px;
        }}
        
        .test-title-large {{
            font-size: 22px;
        }}
        
        .answer-key-table th, .answer-key-table td {{
            padding: 8px 6px;
            font-size: 14px;
        }}
        
        .table-answer-text {{
            padding-left: 8px;
        }}
        
        .solution-header {{
            flex-direction: column;
            align-items: flex-start;
        }}
        
        .solution-number {{
            margin-right: 0;
            margin-bottom: 10px;
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
    <div class='answer-key-section'>
        <div class='answer-key-title'>
            OFFICIAL ANSWER KEY
        </div>
        <div class='test-title-large'>
            {test_title}
        </div>
        <table class='answer-key-table'>
            <thead>
                <tr>
                    <th>Q.No</th>
                    <th>Correct<br>Option</th>
                    <th>Answer</th>
                </tr>
            </thead>
            <tbody>
                {answer_key_rows}
            </tbody>
        </table>
    </div>
    
    <div class='detailed-solutions-section'>
        <div class='detailed-solutions-title'>
            📚 DETAILED SOLUTIONS & EXPLANATIONS
        </div>
    """
    
    for idx, q in enumerate(data, 1):
        correct_answer = None
        correct_option_label = None
        
        alternatives = q.get("alternatives", [])[:4]
        labels = ["A", "B", "C", "D"]
        
        for opt_idx, opt in enumerate(alternatives):
            if opt_idx < len(labels) and str(opt.get("score_if_chosen")) == "1":
                correct_answer = opt.get('answer')
                correct_option_label = labels[opt_idx]
                break
        
        html_template += f"""
        <div class='solution-container'>
            <a href='https://t.me/SAD_LYFFFF' target='_blank' class='question-watermark'>SAD_LYFFFF</a>
            <div class='solution-header'>
                <div class='solution-number'>{idx}</div>
            </div>
            
            <div class='correct-answer-inline'>
                <span class='correct-label'>({correct_option_label or "?"}) -</span><span class='answer-text-inline'>{process_html_content(correct_answer) if correct_answer else "Answer not available"}</span>
            </div>
        """
        
        detailed_solution = str(q.get("detailed_solution", "")).strip() if q.get("detailed_solution") else ""
        solution = str(q.get("solution", "")).strip() if q.get("solution") else ""
        explanation = str(q.get("explanation", "")).strip() if q.get("explanation") else ""
        
        if detailed_solution:
            html_template += f"""
            <div class='solution-section'>
                <div class='solution-label'>
                    💡 Solution
                </div>
                <div class='solution-content'>{process_html_content(detailed_solution)}</div>
            </div>
            """
        elif solution:
            html_template += f"""
            <div class='solution-section'>
                <div class='solution-label'>
                    💡 Solution
                </div>
                <div class='solution-content'>{process_html_content(solution)}</div>
            </div>
            """
        elif explanation:
            html_template += f"""
            <div class='solution-section'>
                <div class='solution-label'>
                    📝 Explanation
                </div>
                <div class='solution-content'>{process_html_content(explanation)}</div>
            </div>
            """
        else:
            html_template += f"""
            <div class='solution-section'>
                <div class='no-solution'>
                    SAD_LYFFFF
                </div>
            </div>
            """
        
        html_template += """
        </div>
        """
    
    html_template += """
    </div>
</body>
</html>
    """
    
    return html_template
    
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation with main menu."""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text(f"🚫 *Access Denied*\n\n❌ You are not authorized to use this bot\.\n\n🆔 *Your User ID:* {update.effective_user.id}", parse_mode='MarkdownV2')
        logger.warning(f"Unauthorized access attempt by user ID: {update.effective_user.id}")
        return ConversationHandler.END

    if not check_internet_connection():
        await update.message.reply_text("🌐 *Network connectivity issue detected\.* Please check your internet connection and try again\. 🔄", parse_mode='MarkdownV2')
        return ConversationHandler.END

    # Create main menu keyboard
    keyboard = [
        [
            InlineKeyboardButton("📚 Extract Test", callback_data="extract_test"),
            InlineKeyboardButton("ℹ️ Get Test Info", callback_data="get_info")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_message = """🤖 *Test Extraction and Info Bot*

👋 *Welcome\!* This bot helps you extract and get information about Aakash iTutor tests\.

✨ *Available Features:*
📚 *Extract Test:* Download test questions in various formats
ℹ️ *Get Test Info:* View detailed test information and syllabus

🚀 *Choose an option below to get started:*"""

    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='MarkdownV2')
    return ConversationHandler.END

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu selections"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "extract_test":
        await query.edit_message_text("📚 *Extract Test*\n\n🔢 Please send the *NID* \\(Numerical ID\\) for the test you want to extract:", parse_mode='MarkdownV2')
        return ASK_NID
    elif query.data == "get_info":
        await query.edit_message_text("ℹ️ *Get Test Info*\n\n🔢 Please send the *NID* \\(Numerical ID\\) to get test information:", parse_mode='MarkdownV2')
        return ASK_INFO_NID
    elif query.data == "help":
        help_text = """❓ *Help \\- How to use this bot*

📚 *Extract Test:*
• Provide test NID
• Choose from 4 different formats
• Download HTML files with questions/answers

ℹ️ *Get Test Info:*
• Provide test NID
• Get detailed test information
• View syllabus and timing details

📋 *Available Extract Formats:*
🎯 *NEET Style:* 2\\-column printable with syllabus
📝 *Questions Only:* Clean format for practice
✅ *Questions \\+ Answers:* Correct answers highlighted
📖 *Complete Solutions:* Answer key \\+ explanations

🔢 *What is NID?*
NID is the unique identifier for each test on Aakash iTutor platform\\.

💡 *Example:* 4342866055"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
        return ConversationHandler.END

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to menu button"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("📚 Extract Test", callback_data="extract_test"),
            InlineKeyboardButton("ℹ️ Get Test Info", callback_data="get_info")
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_message = """🤖 *Test Extraction and Info Bot*

✨ *Available Features:*
📚 *Extract Test:* Download test questions in various formats
ℹ️ *Get Test Info:* View detailed test information and syllabus

🚀 *Choose an option below:*"""

    await query.edit_message_text(welcome_message, reply_markup=reply_markup, parse_mode='MarkdownV2')

async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /extract command"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 *Access Denied*\n\n❌ You are not authorized to use this bot\\.", parse_mode='MarkdownV2')
        return ConversationHandler.END

    await update.message.reply_text("📚 *Extract Test*\n\n🔢 Please send the *NID* \\(Numerical ID\\) for the test you want to extract:", parse_mode='MarkdownV2')
    return ASK_NID

def format_syllabus_for_telegram(description):
    """Format syllabus with proper line breaks for each subject"""
    if not description:
        return "N/A"
    
    try:
        # Clean the description text
        desc_text = clean_text_for_telegram(description)
        
        # Simply add line breaks before subject names and make them bold
        # This approach avoids duplication by not extracting content
        subjects = ['Physics', 'Chemistry', 'Botany', 'Zoology', 'Mathematics', 'Biology']
        
        formatted_text = desc_text
        
        # Add line breaks and bold formatting before each subject (except if it's at the start)
        for subject in subjects:
            # Pattern to match subject name followed by colon, but not at the start of text
            pattern = rf'(\S)\s*({subject})\s*:'
            replacement = rf'\1\n\n<b>\2:</b>'
            formatted_text = re.sub(pattern, replacement, formatted_text, flags=re.IGNORECASE)
        
        # If the first subject doesn't have bold formatting, add it
        for subject in subjects:
            pattern = rf'^({subject})\s*:'
            if re.match(pattern, formatted_text, re.IGNORECASE):
                formatted_text = re.sub(pattern, rf'<b>\1:</b>', formatted_text, flags=re.IGNORECASE)
                break
        
        # Clean up excessive line breaks
        formatted_text = re.sub(r'\n{3,}', '\n\n', formatted_text)
        formatted_text = formatted_text.strip()
        
        return formatted_text
        
    except Exception as e:
        logger.error(f"Error formatting syllabus: {e}")
        return clean_text_for_telegram(description)

def format_quiz_info_html(quiz_data):
    """Format quiz data into readable message using HTML parsing - Fixed version"""
    try:
        # Clean and escape data
        title = clean_text_for_telegram(quiz_data.get('title', 'N/A'))
        description = clean_text_for_telegram(quiz_data.get('description', 'N/A'))
        
        # Format syllabus with proper line breaks
        formatted_syllabus = format_syllabus_for_telegram(description)
        
        # Extract timing information
        quiz_open = format_timestamp(quiz_data.get('quiz_open'))
        quiz_close = format_timestamp(quiz_data.get('quiz_close'))
        show_results = format_timestamp(quiz_data.get('show_results'))
        
        # Create formatted message using HTML (more reliable than MarkdownV2)
        formatted_message = f"""📋 <b>QUIZ INFORMATION</b>

📚 <b>Test Name:</b>
{title}

📝 <b>Description and Syllabus:</b>
{formatted_syllabus}

⏰ <b>Timing Information:</b>
🕐 <b>Opens:</b> {quiz_open}
🔒 <b>Closes:</b> {quiz_close}
📊 <b>Results:</b> {show_results}"""
        
        return formatted_message.strip()
    
    except Exception as e:
        logger.error(f"Error formatting quiz data with HTML: {e}")
        return format_quiz_info_plain(quiz_data)

def format_syllabus_for_telegram_plain(description):
    """Format syllabus with proper line breaks for each subject - Plain text version"""
    if not description:
        return "N/A"
    
    try:
        # Clean the description text
        desc_text = clean_text_for_telegram(description)
        
        # Check if it contains subject-wise syllabus
        subjects = ['Physics', 'Chemistry', 'Botany', 'Zoology', 'Mathematics', 'Biology']
        
        # If we can identify subjects in the description, format them properly
        formatted_lines = []
        
        # Split by common delimiters that separate subjects
        # Try to detect subject patterns
        for subject in subjects:
            pattern = rf'{subject}\s*[:\-]\s*(.*?)(?=(?:Physics|Chemistry|Botany|Zoology|Mathematics|Biology)[:\-]|$)'
            match = re.search(pattern, desc_text, re.IGNORECASE | re.DOTALL)
            
            if match:
                subject_content = match.group(1).strip()
                # Clean up the content
                subject_content = re.sub(r'\s+', ' ', subject_content)
                subject_content = subject_content.rstrip(',').strip()
                
                if subject_content and len(subject_content) > 2:
                    formatted_lines.append(f"{subject}: {subject_content}")
        
        # If we found subject-wise breakdown, return formatted version
        if formatted_lines:
            return '\n\n'.join(formatted_lines)
        
        # If no clear subject breakdown, try to add line breaks at logical points
        # Look for patterns that suggest new topics or subjects
        formatted_text = desc_text
        
        # Add line breaks before subject names if they appear mid-sentence
        for subject in subjects:
            formatted_text = re.sub(
                rf'(\w)\s+({subject})\s*:', 
                rf'\1\n\n\2:', 
                formatted_text, 
                flags=re.IGNORECASE
            )
        
        # Add line breaks before common topic indicators
        formatted_text = re.sub(r'(\w)\s+(Unit\s+\d+|Chapter\s+\d+|Topic\s+\d+)', r'\1\n\n\2', formatted_text)
        
        # Ensure we don't have excessive line breaks
        formatted_text = re.sub(r'\n{3,}', '\n\n', formatted_text)
        
        return formatted_text.strip()
        
    except Exception as e:
        logger.error(f"Error formatting syllabus plain: {e}")
        return clean_text_for_telegram(description)

def format_quiz_info_plain(quiz_data):
    """Plain text formatting (no markdown/HTML) as fallback - Fixed version"""
    try:
        title = clean_text_for_telegram(quiz_data.get('title', 'N/A'))
        description = clean_text_for_telegram(quiz_data.get('description', 'N/A'))
        
        # Format syllabus with proper line breaks (plain text version)
        formatted_syllabus = format_syllabus_for_telegram_plain(description)
        
        # Extract timing information
        quiz_open = format_timestamp(quiz_data.get('quiz_open'))
        quiz_close = format_timestamp(quiz_data.get('quiz_close'))
        show_results = format_timestamp(quiz_data.get('show_results'))
        
        # Create formatted message in plain text
        formatted_message = f"""📋 QUIZ INFORMATION

📚 Test Name:
{title}

📝 Description and Syllabus:
{formatted_syllabus}

⏰ Timing Information:
🕐 Opens: {quiz_open}
🔒 Closes: {quiz_close}
📊 Results: {show_results}"""
        
        return formatted_message.strip()
    
    except Exception as e:
        logger.error(f"Error in plain formatting: {e}")
        return f"⚠️ Error formatting quiz data. NID: {quiz_data.get('nid', 'unknown')}"

def clean_text_for_telegram_enhanced(text):
    """Enhanced text cleaning specifically for Telegram messages"""
    if not text:
        return "N/A"
    
    # Convert to string and basic cleaning
    text = str(text).strip()
    
    # Remove HTML tags first
    text = re.sub(r'<[^>]*>', '', text)
    
    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    
    # Remove problematic characters that can cause parsing issues
    # Keep basic punctuation but remove special characters
    text = re.sub(r'[^\w\s\-\.\(\),:\n/&]', '', text)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n+', '\n', text).strip()
    
    # Remove any remaining escape characters
    text = text.replace('\\', '')
    
    # Limit length to prevent issues (Telegram has message limits)
    if len(text) > 800:
        text = text[:797] + "..."
    
    return text if text else "N/A"

# Updated info command handler
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /info command with NID parameter - Fixed formatting version"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Access Denied\n\n❌ You are not authorized to use this bot.")
        return

    try:
        if not context.args:
            await update.message.reply_text(
                "⚠️ <b>Please provide an NID!</b>\n\n"
                "📝 <b>Usage:</b> /info &lt;NID&gt;\n"
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
        
        quiz_data = await fetch_quiz_info(nid)
        
        if quiz_data:
            # Use HTML formatting (most reliable)
            formatted_info = format_quiz_info_html(quiz_data)
            await loading_message.edit_text(formatted_info, parse_mode='HTML')
        else:
            await loading_message.edit_text(
                f"❌ <b>No quiz found with NID:</b> {nid}\n\n"
                f"🔍 Please check the NID and try again.",
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"Error in info_command: {e}")
        try:
            # Try to send error message with HTML formatting
            await update.message.reply_text(
                f"⚠️ <b>An error occurred:</b>\n{str(e)[:200]}\n\n"
                f"🔄 Please try again later.",
                parse_mode='HTML'
            )
        except:
            # Final fallback to plain text if HTML also fails
            await update.message.reply_text(
                f"⚠️ An error occurred: {str(e)[:200]}\n\n"
                f"🔄 Please try again later."
            )

# Updated handle_info_nid function
async def handle_info_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle NID input for info command - Fixed formatting version"""
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
            formatted_info = format_quiz_info_html(quiz_data)
            await loading_message.edit_text(formatted_info, parse_mode='HTML')
        else:
            await loading_message.edit_text(
                f"❌ <b>No quiz found with NID:</b> {nid}\n\n"
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
            await loading_message.edit_text(
                f"⚠️ An error occurred: {str(e)[:200]}\n\n"
                f"🔄 Please try again later."
            )
    
    return ConversationHandler.END

# Also update the clean_text_for_telegram function
def clean_text_for_telegram(text):
    """Clean text to prevent Telegram parsing errors - Enhanced version"""
    if not text:
        return "N/A"
    
    # Convert to string and basic cleaning
    text = str(text).strip()
    
    # Remove HTML tags
    text = re.sub(r'<[^>]*>', '', text)
    
    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    
    # Remove problematic characters that can cause parsing issues
    # Keep more characters but remove the most problematic ones
    text = re.sub(r'[<>{}[\]\\`*_~|]', '', text)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n+', '\n', text).strip()
    
    # Limit length to prevent issues
    if len(text) > 800:
        text = text[:797] + "..."
    
    return text if text else "N/A"

async def handle_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the NID from the user for extraction."""
    nid = update.message.text.strip()
    if not nid.isdigit():
        await update.message.reply_text("❌ Invalid NID. Please send a numerical ID. 🔢")
        return ASK_NID

    context.user_data['nid'] = nid
    
    # Create inline keyboard for format selection
    keyboard = [
        [
            InlineKeyboardButton("🎯 NEET Style (with Syllabus)", callback_data="neet_style"),
            InlineKeyboardButton("📝 Questions Only", callback_data="questions_only")
        ],
        [
            InlineKeyboardButton("✅ Questions + Answers", callback_data="questions_answers"),
            InlineKeyboardButton("📖 Complete Solutions", callback_data="solutions_only")
        ],
        [
            InlineKeyboardButton("📦 All Formats (4 files)", callback_data="all_formats")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📋 Choose your preferred format:\n\n"
        "🎯 NEET Style: 2-column printable format with syllabus\n"
        "📝 Questions Only: Clean format, no answers shown\n"
        "✅ Questions + Answers: Correct answers highlighted\n"
        "📖 Complete Solutions: Answer key + detailed explanations\n"
        "📦 All Formats: Get all 4 files at once",
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
    
    await query.edit_message_text("⚙️ Processing your request... This may take a moment due to network conditions... ⏳")

    if not check_internet_connection():
        await query.edit_message_text("🌐 Network connectivity lost. Please check your internet connection and try again. 🔄")
        return ConversationHandler.END

    try:
        # Fetch data
        title, desc = fetch_test_title_and_description(nid)
        data = fetch_locale_json_from_api(nid)
        
        if not data:
            await query.edit_message_text(
                "❌ Extraction failed: No questions found for the specified NID.\n\n"
                "🔍 This could be due to:\n"
                "• Network connectivity issues\n"
                "• Invalid NID\n" 
                "• Server temporarily unavailable\n\n"
                "🔄 Please verify the NID and try again later."
            )
            return ConversationHandler.END

        # Clean title for filename
        clean_title = re.sub(r'[\\/*?:"<>|]', "_", title)
        if not clean_title or len(clean_title) > 50:
            clean_title = f"Test_{nid}"

        # Generate files based on selection
        if format_choice == "neet_style":
            syllabus_data = fetch_syllabus_data(nid)
            content = generate_neet_style_paper_with_syllabus(data, title, syllabus_data)
            filename = f"{clean_title}_NEET_Style_with_Syllabus.html"
            caption = "🎯 NEET Style Question Paper with Syllabus!\n📄 2-column printable format\n📚 Includes syllabus section"
            
            await update.effective_chat.send_document(
                document=BytesIO(content.encode("utf-8")),
                filename=filename,
                caption=caption
            )
            
        elif format_choice == "questions_only":
            content = generate_questions_only_html(data, title)
            filename = f"{clean_title}_Questions_Only.html"
            caption = "📝 Questions Only!\n❌ No answers or solutions - Perfect for practice tests"
            
            await update.effective_chat.send_document(
                document=BytesIO(content.encode("utf-8")),
                filename=filename,
                caption=caption
            )
            
        elif format_choice == "questions_answers":
            content = generate_questions_with_answers_html(data, title)
            filename = f"{clean_title}_Questions_with_Answers.html"
            caption = "✅ Questions with Correct Answers Marked!\n🎯 Answers marked but no detailed solutions"
            
            await update.effective_chat.send_document(
                document=BytesIO(content.encode("utf-8")),
                filename=filename,
                caption=caption
            )
            
        elif format_choice == "solutions_only":
            content = generate_solutions_html(data, title)
            filename = f"{clean_title}_Complete_Solutions.html"
            caption = "📖 Complete Solutions with Answer Key Table!\n📊 Answer key table + detailed explanations and solutions"
            
            await update.effective_chat.send_document(
                document=BytesIO(content.encode("utf-8")),
                filename=filename,
                caption=caption
            )
            
        elif format_choice == "all_formats":
            # Generate all 4 formats
            await query.edit_message_text("⚙️ Generating all 4 formats... Please wait... ⏳")
            
            # 1. NEET Style
            syllabus_data = fetch_syllabus_data(nid)
            neet_content = generate_neet_style_paper_with_syllabus(data, title, syllabus_data)
            await update.effective_chat.send_document(
                document=BytesIO(neet_content.encode("utf-8")),
                filename=f"{clean_title}_NEET_Style_with_Syllabus.html",
                caption="🎯 NEET Style with Syllabus - 2-column printable format"
            )
            
            # 2. Questions Only
            questions_content = generate_questions_only_html(data, title)
            await update.effective_chat.send_document(
                document=BytesIO(questions_content.encode("utf-8")),
                filename=f"{clean_title}_Questions_Only.html",
                caption="📝 Questions Only - No answers shown (for practice)"
            )
            
            # 3. Questions with Answers
            answers_content = generate_questions_with_answers_html(data, title)
            await update.effective_chat.send_document(
                document=BytesIO(answers_content.encode("utf-8")),
                filename=f"{clean_title}_Questions_with_Answers.html",
                caption="✅ Questions with Correct Answers - Highlighted correct options"
            )
            
            # 4. Complete Solutions
            solutions_content = generate_solutions_html(data, title)
            await update.effective_chat.send_document(
                document=BytesIO(solutions_content.encode("utf-8")),
                filename=f"{clean_title}_Complete_Solutions.html",
                caption="📖 Complete Solutions - Answer key + detailed explanations"
            )

    except Exception as e:
        error_msg = (
            f"❌ Extraction Failed\n\n"
            f"📋 Error Details:\n"
            f"• NID: {nid}\n"
            f"• Error: {str(e)[:100]}...\n\n"
            f"🔧 Possible Solutions:\n"
            f"• Check your internet connection\n"
            f"• Verify the NID is correct\n"
            f"• Try again in a few moments\n"
            f"• Contact support if the issue persists\n\n"
            f"🚀 Use /start to try again"
        )
        
        await update.effective_chat.send_message(text=error_msg)
        logger.error(f"Error processing NID {nid}: {str(e)}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the conversation."""
    await update.message.reply_text("❌ Operation Cancelled\n\n🚀 Use /start to begin a new operation")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows help information."""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Access Denied. You are not authorized to use this bot.")
        return

    help_text = (
        "❓ Test Extraction and Info Bot - Help Guide\n\n"
        "🤖 Available Commands:\n"
        "• /start - Show main menu\n"
        "• /extract - Begin test extraction process\n"
        "• /info <NID> - Get test information (e.g., /info 4342866055)\n"
        "• /help - Show this help message\n"
        "• /cancel - Cancel current operation\n\n"
        "📋 Available Extract Formats:\n"
        "🎯 NEET Style: 2-column printable format with syllabus\n"
        "📝 Questions Only: Clean format for practice tests\n"
        "✅ Questions + Answers: Correct answers highlighted\n"
        "📖 Complete Solutions: Answer key + detailed explanations\n"
        "📦 All Formats: Get all 4 files at once\n\n"
        "🚀 How to Extract:\n"
        "1️⃣ Send /start or /extract to begin\n"
        "2️⃣ Provide the NID (test ID number)\n"
        "3️⃣ Choose your preferred format\n"
        "4️⃣ Receive your generated files!\n\n"
        "ℹ️ How to Get Info:\n"
        "• Send /info <NID> directly\n"
        "• Or use /start → Get Test Info\n\n"
        "⚠️ Important Notes:\n"
        "• Only authorized users can access this bot\n"
        "• Ensure stable internet connection\n"
        "• Valid NID is required for all operations\n"
        "• Files are automatically named with test title\n"
        "• Files are generated in HTML format\n\n"
        "🆘 Need Support?\n"
        "Contact: @SAD_LYFFFF"
    )
    
    await update.message.reply_text(help_text)

async def handle_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages from unauthorized users."""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text(
            f"🚫 Access Denied\n\n"
            f"❌ This bot is restricted to authorized users only.\n"
            f"👤 Contact the administrator for access.\n\n"
            f"🆔 Your User ID: {update.effective_user.id}"
        )
        logger.warning(f"Unauthorized access attempt by user ID: {update.effective_user.id}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles errors that occur during bot operation."""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_chat:
        try:
            error_message = (
                "⚠️ An unexpected error occurred\n\n"
                "🔧 What you can do:\n"
                "• Try the operation again\n"
                "• Check your internet connection\n"
                "• Use /start to begin fresh\n\n"
                "🆘 If the problem persists, contact support"
            )
            await update.effective_chat.send_message(text=error_message)
        except Exception as send_error:
            logger.error(f"Could not send error message: {send_error}")
def main() -> None:
    """Main function to run the bot."""
    # Initial connection tests
    print("🌐 Testing network connectivity...")
    if not check_internet_connection():
        print("❌ Network connectivity test failed!")
        print("🔄 Please check your internet connection and try again.")
        return
    print("✅ Network connectivity test passed!")

    print("🤖 Testing Telegram API connectivity...")
    if not test_telegram_api(BOT_TOKEN):
        print("❌ Telegram API test failed!")
        print("🔑 Please check your bot token and try again.")
        return
    print("✅ Telegram API test passed!")

    # Create application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Create conversation handler for extraction
    extract_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("extract", extract_command),
            CallbackQueryHandler(handle_main_menu, pattern="^(extract_test|get_info|help)$"),
            CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$")
        ],
        states={
            ASK_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_nid)],
            ASK_INFO_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_info_nid)],
            CHOOSE_FORMAT: [CallbackQueryHandler(handle_format_choice)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
    )

    # Add handlers
    application.add_handler(extract_conv_handler)
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.ALL, handle_unauthorized))
    
    # Add error handler
    application.add_error_handler(error_handler)

    # Start the bot
    print("🚀 Starting Test Extraction and Info Bot...")
    print(f"👥 Authorized users: {AUTHORIZED_USER_IDS}")
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    
    try:
        # Run the bot with error handling
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
    except Conflict:
        print("⚠️ Bot is already running elsewhere! Please stop other instances.")
    except NetworkError as e:
        print(f"🌐 Network error: {e}")
        print("🔄 Please check your internet connection and try again.")
    except TimedOut as e:
        print(f"⏰ Connection timeout: {e}")
        print("🔄 Please check your network stability and try again.")
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped by user.")
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        logger.error(f"Unexpected error in main: {e}")

if __name__ == "__main__":
    main()