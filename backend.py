import json
import mimetypes
import os
import re
import urllib.request
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Directory where index.html and app.js live (same folder as this script)
STATIC_DIR = Path(__file__).parent

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# In-memory storage for real saved meetings
SAVED_MEETINGS = []

# Demo Dataset
DEMO_MEETINGS = [
    {
        "id": "demo-1",
        "title": "Demo Project Review Meeting",
        "date": "2026-09-01",
        "duration": "00:15:30",
        "participants": ["Speaker 1", "Speaker 2"],
        "meetingType": "Project Review",
        "platform": "Demo Recording",
        "actionItemsCount": 1,
        "status": "Completed",
        "summary": "Demonstration meeting highlighting Gemini AI speech summarization and extractive action item generation.",
        "discussionPoints": [
            {"topic": "System Validation", "description": "Evaluating speaker diarization with real media timestamps."}
        ],
        "decisions": [
            "Complete evaluation benchmarks by Friday."
        ],
        "actionItems": [
            {"id": "act-d1", "task": "Review final minutes export", "owner": "Speaker 1", "deadline": "Friday", "priority": "High", "status": "Pending", "source_timestamp": "00:04:12"}
        ],
        "speakers": [
            {"id": "spk-1", "name": "Speaker 1", "speakingTimePct": 60, "words": 120},
            {"id": "spk-2", "name": "Speaker 2", "speakingTimePct": 40, "words": 80}
        ],
        "transcript": [
            {"id": "tr-1", "speaker": "Speaker 1", "time": "00:00:05", "start_time": 5, "end_time": 15, "text": "Welcome to the demonstration. We need to complete evaluation benchmarks by Friday."},
            {"id": "tr-2", "speaker": "Speaker 2", "time": "00:04:12", "start_time": 252, "end_time": 260, "text": "I will review the final minutes export."}
        ],
        "aiModelUsed": "Google Gemini AI (Demo)",
        "isDemo": True
    }
]

def format_seconds_to_hhmmss(seconds):
    try:
        sec = int(float(seconds))
        hrs = sec // 3600
        mins = (sec % 3600) // 60
        secs = sec % 60
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    except Exception:
        return "00:00:00"

def parse_transcript_text(raw_text):
    if not raw_text or not raw_text.strip():
        return []

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    utterances = []
    current_speaker = "Speaker 1"
    acc_time = 0

    for i, line in enumerate(lines):
        ts_spk_match = re.match(r"^\[?\(?(\d{1,2}:\d{2}(?::\d{2})?)\)?\]?\s*[-:]?\s*([^:]+):\s*(.+)$", line)
        spk_match = re.match(r"^([^:\[\(]{2,30}):\s*(.+)$", line)
        ts_match = re.match(r"^\[?\(?(\d{1,2}:\d{2}(?::\d{2})?)\)?\]?\s*(.+)$", line)

        if ts_spk_match:
            time_str, spk, txt = ts_spk_match.group(1), ts_spk_match.group(2).strip(), ts_spk_match.group(3).strip()
            parts = [int(p) for p in time_str.split(":")]
            sec = parts[0]*3600 + parts[1]*60 + parts[2] if len(parts)==3 else parts[0]*60 + parts[1]
            utterances.append({
                "id": f"tr-{i+1}",
                "speaker": spk,
                "time": format_seconds_to_hhmmss(sec),
                "start_time": sec,
                "end_time": sec + 10,
                "text": txt
            })
            acc_time = sec + 10
        elif spk_match and not spk_match.group(1).lower().startswith("http"):
            spk, txt = spk_match.group(1).strip(), spk_match.group(2).strip()
            utterances.append({
                "id": f"tr-{i+1}",
                "speaker": spk,
                "time": format_seconds_to_hhmmss(acc_time),
                "start_time": acc_time,
                "end_time": acc_time + 8,
                "text": txt
            })
            acc_time += 8
        elif ts_match:
            time_str, txt = ts_match.group(1), ts_match.group(2).strip()
            parts = [int(p) for p in time_str.split(":")]
            sec = parts[0]*3600 + parts[1]*60 + parts[2] if len(parts)==3 else parts[0]*60 + parts[1]
            utterances.append({
                "id": f"tr-{i+1}",
                "speaker": current_speaker,
                "time": format_seconds_to_hhmmss(sec),
                "start_time": sec,
                "end_time": sec + 8,
                "text": txt
            })
            acc_time = sec + 8
        else:
            if i > 0 and i % 3 == 0:
                current_speaker = "Speaker 2" if current_speaker == "Speaker 1" else "Speaker 1"
            utterances.append({
                "id": f"tr-{i+1}",
                "speaker": current_speaker,
                "time": format_seconds_to_hhmmss(acc_time),
                "start_time": acc_time,
                "end_time": acc_time + 8,
                "text": line
            })
            acc_time += 8

    return utterances


class GeminiMeetingSummarizer:
    CANDIDATE_MODELS = [
        "models/gemini-3.5-flash",
        "models/gemini-3-flash-preview",
        "models/gemini-3.7-flash",
        "models/gemini-flash-latest"
    ]

    @classmethod
    def call_gemini(cls, transcript_text, meeting_title, participants, api_key=None):
        key = api_key or GEMINI_API_KEY
        if not key:
            raise ValueError("No Gemini API key provided.")

        prompt = f"""You are an expert AI meeting documentation analyst.
Analyze the following timestamped meeting transcript and generate thorough, structured meeting minutes strictly based on the spoken conversation.

Meeting Title: {meeting_title}
Participants: {', '.join(participants)}

Transcript:
{transcript_text}

CRITICAL REQUIREMENTS:
1. "summary": A cohesive, high-level executive summary paragraph capturing the meeting context and main outcomes.
2. "discussionPoints": Extract EVERY distinct topic discussed as a separate object. For each topic, provide a descriptive, specific title in "topic", and in "description" provide a detailed, multi-sentence summary with concrete facts, arguments, proposals, and considerations. DO NOT just repeat or rephrase the executive summary.
3. "decisions": Array of concrete decisions agreed upon or confirmed during the meeting.
4. "actionItems": Array of specific actionable tasks with owner, deadline, priority (High/Medium/Low), and source_timestamp.

Return strictly valid JSON matching this schema:
{{
  "summary": "Executive summary paragraph...",
  "discussionPoints": [
    {{"topic": "Specific Topic Heading", "description": "Detailed discussion breakdown, context, and findings..."}}
  ],
  "decisions": [
    "Decision confirmed during meeting..."
  ],
  "actionItems": [
    {{"task": "Task description", "owner": "Owner or Speaker name", "deadline": "Deadline or Not specified", "priority": "High/Medium/Low", "source_timestamp": "HH:MM:SS"}}
  ]
}}"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json"
            }
        }

        last_error = None
        for model in cls.CANDIDATE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(raw_text)
            except Exception as e:
                last_error = e
                print(f"Model {model} failed in summarization: {e}")

        raise last_error or RuntimeError("All Gemini models failed.")

    @classmethod
    def diarize_speakers(cls, transcript_or_utterances, api_key=None):
        key = api_key or GEMINI_API_KEY
        if not key:
            raise ValueError("No Gemini API key provided.")

        if isinstance(transcript_or_utterances, list):
            raw_text = "\n".join([f"[{u.get('time', '00:00:00')}] {u.get('speaker', 'Speaker 1')}: {u.get('text', '')}" for u in transcript_or_utterances])
        else:
            raw_text = str(transcript_or_utterances)

        prompt = f"""You are an expert conversation and speech diarization AI.
Below is a raw meeting transcript where speakers may be incorrectly labeled or merged under a single speaker:

{raw_text}

Analyze the dialogue patterns, conversational flow, questions, responses, greetings, agreement, and distinct voice perspectives.
Separate the transcript into distinct speakers (Speaker 1, Speaker 2, Speaker 3, etc.).
Return strictly valid JSON in this format:
{{
  "turns": [
    {{"speaker": "Speaker 1", "text": "Spoken segment...", "time": "HH:MM:SS"}}
  ]
}}"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json"
            }
        }

        for model in cls.CANDIDATE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(raw_text)
                    return parsed.get("turns", [])
            except Exception as e:
                print(f"Model {model} failed in diarization: {e}")

        return []


class RealMeetingProcessor:
    @classmethod
    def process(cls, payload):
        transcript_text = payload.get("transcript_text", "")
        source_type = payload.get("source_type", "transcript")
        meeting_link = payload.get("meeting_link", "")
        speaker_mapping = payload.get("speaker_mapping", {})
        title = payload.get("title", "").strip() or "Untitled Meeting"
        meeting_type = payload.get("meeting_type", "Team Meeting")
        date_str = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
        api_key = payload.get("gemini_api_key") or GEMINI_API_KEY

        if source_type == "link" and meeting_link:
            valid_domains = ["meet.google.com", "zoom.us", "teams.microsoft.com"]
            if not any(d in meeting_link.lower() for d in valid_domains):
                raise ValueError("Unable to access this meeting link. Please verify the URL.")

        utterances = parse_transcript_text(transcript_text)
        if not utterances:
            if source_type == "link":
                utterances = [{
                    "id": "tr-1", "speaker": "Speaker 1", "time": "00:00:00",
                    "start_time": 0, "end_time": 10,
                    "text": f"Meeting session connected via {meeting_link}."
                }]
            else:
                raise ValueError("Transcription could not be completed. No speech or text was provided.")

        for u in utterances:
            u["speaker"] = speaker_mapping.get(u["speaker"], u["speaker"])

        # Auto-diarize if only 1 speaker detected but multiple statements exist
        distinct_speakers = set(u["speaker"] for u in utterances)
        if len(distinct_speakers) <= 1 and len(utterances) >= 2 and api_key:
            try:
                diarized_turns = GeminiMeetingSummarizer.diarize_speakers(utterances, api_key)
                if diarized_turns and len(set(t.get("speaker") for t in diarized_turns)) > 1:
                    new_utterances = []
                    acc = 0
                    for idx, t in enumerate(diarized_turns):
                        t_time = t.get("time") or format_seconds_to_hhmmss(acc)
                        new_utterances.append({
                            "id": f"tr-ai-{idx+1}",
                            "speaker": speaker_mapping.get(t.get("speaker"), t.get("speaker", "Speaker 1")),
                            "time": t_time,
                            "start_time": acc,
                            "end_time": acc + 10,
                            "text": t.get("text", "")
                        })
                        acc += 10
                    utterances = new_utterances
            except Exception as ex:
                print("Auto-diarization fallback:", ex)

        word_counts = {}
        total_words = 0
        for u in utterances:
            count = len(u["text"].split())
            word_counts[u["speaker"]] = word_counts.get(u["speaker"], 0) + count
            total_words += count

        speakers = []
        for idx, (spk, count) in enumerate(word_counts.items()):
            pct = round((count / total_words) * 100) if total_words > 0 else 100
            speakers.append({
                "id": f"spk-{idx+1}",
                "name": spk,
                "speakingTimePct": pct,
                "words": count
            })

        full_transcript = "\n".join([f"[{u['time']}] {u['speaker']}: {u['text']}" for u in utterances])
        participant_names = [s["name"] for s in speakers]

        ai_model_used = "Google Gemini AI"
        try:
            gemini_res = GeminiMeetingSummarizer.call_gemini(full_transcript, title, participant_names, api_key)
            summary = gemini_res.get("summary", "Summary processed by Gemini.")
            discussion_points = gemini_res.get("discussionPoints", [])
            decisions = gemini_res.get("decisions", [])
            action_items = [
                {
                    "id": f"act-gemini-{i+1}",
                    "task": a.get("task", ""),
                    "owner": a.get("owner", "Unassigned"),
                    "deadline": a.get("deadline", "Not specified"),
                    "priority": a.get("priority", "Medium"),
                    "status": "Pending",
                    "source_timestamp": a.get("source_timestamp", "00:00:00")
                }
                for i, a in enumerate(gemini_res.get("actionItems", []))
            ]
            ai_model_used = "Google Gemini AI (Gemma 4 / 2.5 Engine)"
        except Exception as e:
            print("Gemini API call fallback:", e)
            sentences = [s.strip() for u in utterances for s in re.split(r"(?<=[.!?])\s+", u["text"]) if len(s.strip()) > 10]
            summary = " ".join(sentences[:3]) if sentences else "Transcript processed."
            discussion_points = [{"topic": f"Discussion Segment ({s['name']})", "description": "Spoken segment"} for s in speakers]
            decisions = [s for s in sentences if any(m in s.lower() for m in ["agreed", "decided", "will", "confirmed"])]
            action_items = []
            ai_model_used = "Local Extractive NLP Parser (Offline Fallback)"

        duration_sec = utterances[-1]["end_time"] if utterances else 0
        meeting_obj = {
            "id": f"meet-{int(datetime.now().timestamp())}",
            "title": title,
            "date": date_str,
            "duration": format_seconds_to_hhmmss(duration_sec),
            "meetingType": meeting_type,
            "platform": meeting_link if source_type == "link" else "Uploaded Transcript / Audio",
            "participants": participant_names,
            "summary": summary,
            "discussionPoints": discussion_points,
            "decisions": decisions,
            "actionItems": action_items,
            "actionItemsCount": len(action_items),
            "speakers": speakers,
            "transcript": utterances,
            "status": "Completed",
            "aiModelUsed": ai_model_used,
            "isDemo": False
        }
        return meeting_obj


class APIHandler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/saved-meetings'):
            is_demo = "demo=true" in self.path
            meetings = DEMO_MEETINGS if is_demo else SAVED_MEETINGS
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "meetings": meetings}).encode('utf-8'))
        else:
            # Serve static frontend files
            url_path = self.path.split('?')[0]  # strip query string
            if url_path == '/' or url_path == '':
                url_path = '/index.html'
            file_path = STATIC_DIR / url_path.lstrip('/')
            if file_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(file_path))
                mime_type = mime_type or 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', mime_type)
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))

    def do_POST(self):
        if self.path in ['/api/diarize']:
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                payload = json.loads(body.decode('utf-8')) if body else {}
                transcript = payload.get("transcript", "")
                key = payload.get("gemini_api_key") or GEMINI_API_KEY
                turns = GeminiMeetingSummarizer.diarize_speakers(transcript, key)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "turns": turns}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
        elif self.path in ['/api/meeting/process', '/api/generate-minutes']:
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                payload = json.loads(body.decode('utf-8')) if body else {}

                result = RealMeetingProcessor.process(payload)
                SAVED_MEETINGS.insert(0, result)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": result}).encode('utf-8'))
            except ValueError as ve:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(ve)}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Server error: {str(e)}"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode('utf-8'))


def run_server(port=None):
    if port is None:
        port = int(os.environ.get('PORT', 5000))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, APIHandler)
    print(f"AI Meeting Minutes running on http://0.0.0.0:{port}")
    httpd.serve_forever()

if __name__ == '__main__':
    run_server()
