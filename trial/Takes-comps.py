import os
import wave
import json
import hashlib

# pip install pdfplumber
import pdfplumber
import re



# Folder containing all take files (.wav)
TAKES_FOLDER = os.path.join("data", "run_run_run", "takes")          # Can be change later to a different folder if needed

# Folder containing all cue files (.wav)
CUES_FOLDER = os.path.join("data", "run_run_run", "cue")

# Folder containing all comp files (.pdf)
COMPS_FOLDER = os.path.join("data", "run_run_run", "comp")          # Can be change later to a different folder if needed

# Where to save the generated evidence bundle
OUTPUT_FILE = os.path.join("data", "run_run_run", "evidence_bundle.json")

def calculate_sha256(file_path):
    """
    Calculates the SHA-256 hash of a file.

    This gives us a way to identify the exact file and detect
    whether the file has been changed later.
    """

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        while True:
            data = file.read(8192)

            if not data:
                break

            sha256.update(data)

    return sha256.hexdigest()

# Wave file parsing
def get_wave_data(file_path):
    """
    Reads a .wav file and returns its parameters and frames.
    """
    try:
        with wave.open(file_path, 'rb') as wav_file:
            channels = wav_file.getnchannels()          # 1 = mono, 2 = stereo
            sample_width = wav_file.getsampwidth()      # In bytes
            sample_rate = wav_file.getframerate()       # Frames per second
            num_frames = wav_file.getnframes()          # Total number of frames

            duration = num_frames / sample_rate         # Duration in seconds
            bit_depth = sample_width * 8                # Bit depth in bits (1 byte = 8 bits)

            artifact = {

                "artefact_hash": calculate_sha256(file_path),

                "artefact_type": "audio/raw-take",

                "attributes": {

                    "creation_method": "recorded",

                    "technical": {

                        "media_type": "audio/wav",

                        "duration_seconds": duration,

                        "sample_rate_hz": sample_rate,

                        "channels": channels,

                        "bit_depth": bit_depth,

                        "format": "WAV",

                        "codec_subtype": None,

                        "file_size_bytes": os.path.getsize(
                            file_path
                        )
                    },

                    "production": {

                        "track_name": os.path.basename(
                            file_path
                        ),

                        "source_role": "take",

                        "instrument": None,

                        "capture_method": None,

                        "equipment": []
                    },

                    "source_metadata": {

                        "original_filename":
                            os.path.basename(file_path)
                    },

                    "provenance": {

                        "present": False,

                        "active_manifest_label": None
                    },

                    "parser_diagnostics": {

                        "parser": "python-wave",

                        "warnings": []
                    }
                },

                "evidence": []
            }

            return artifact

    except wave.Error as e:                             #   Error bad :<
        print(f"Error reading wave file: {e}")
        return None

def scan_directory(path):
    """
    Scans the given directory for .wav files and returns their wave data.
    """
    wave_list = []                                      # List to hold wave data dictionaries
    for root, folders, files in os.walk(path):          # loop through all files in the directory and its subdirectories
        for file in files:                              # Check if the file is a .wav file
            if file.lower().endswith('.wav'):           # Check if the file has a .wav extension
                file_path = os.path.join(root, file)    # Get the full path of the file
                wave_data = get_wave_data(file_path)
                if wave_data:
                    wave_list.append(wave_data)         # Add the wave data to the list if it's not None

    return wave_list
# =========================

# Helper function for CueSheet and CompSheet parsing
def extract_song_title(text):
    """
    Extracts a title inside quotation marks.
    """

    for line in text.splitlines():

        match = re.search(
            r"[‘'“\"](.+?)[’'”\"]",
            line
        )

        if match:

            return match.group(1).strip()

    return None

def extract_bpm(text):
    """
    Finds BPM anywhere in the PDF text.

    Supports:

    approx 87bpm
    87 bpm
    BPM 87
    BPM: 87
    """

    bpm_match = re.search(

        r"(?:"
        r"\bbpm\s*:?\s*(\d+(?:\.\d+)?)"
        r"|"
        r"(\d+(?:\.\d+)?)\s*bpm\b"
        r")",

        text,

        re.IGNORECASE
    )

    if bpm_match:

        bpm_value = (
            bpm_match.group(1)
            or
            bpm_match.group(2)
        )

        return float(bpm_value)

    return None

def extract_key(text):
    """
    Extracts the first value inside parentheses.
    """

    match = re.search(
        r"\(\s*([^,\n]+?)\s*,",
        text
    )

    if match:

        return match.group(1).strip()

    return None
# ==========================

# CueSheet Parsing
def read_cue_sheet(file_path):
    """
    Reads a CueSheet PDF.
    """
    song_title = None

    key = None

    tempo_bpm = None

    musical_structure = []

    with pdfplumber.open(file_path) as pdf:
        full_text = ""

        for page in pdf.pages:
            page_text = page.extract_text()

            if page_text:
                full_text += (page_text + "\n")

        # Title
        song_title = extract_song_title(full_text)

        # Key
        key = extract_key(full_text)

        # BPM
        tempo_bpm = extract_bpm(full_text)

        for page in pdf.pages:
            tables = page.extract_tables()

            for table in tables:
                for row in table:
                    if not row:
                        continue


                    row = [
                        cell.strip()
                        if cell
                        else ""

                        for cell in row
                    ]

                    if len(row) < 3:
                        continue

                    time_value = row[0]

                    section = row[2]

                    # Ignore the table header
                    if time_value.lower() == "time":
                        continue

                    if not re.match(r"^\d+:\d{2}$", time_value):
                        continue


                    if not section:
                        continue

                    section_name = re.split(r'[:“"]', section, maxsplit=1)[0].strip()

                    if section_name:
                        musical_structure.append(section_name)

    return {
        "document": {
            "song_title": song_title,
            "style": None,
            "key": key,
            "tempo_bpm": tempo_bpm,
            "musical_structure":
                musical_structure
        },

        "source_metadata": {

            "original_filename":
                os.path.basename(file_path)
        }
    }

def scan_cues_directory(path):
    """
    Scans a folder for CueSheet PDFs.
    """
    cue_sheets = []

    for root, folders, files in os.walk(path):
        for file in sorted(files):
            if file.lower().endswith(".pdf"):
                file_path = os.path.join(root, file)
                try:
                    cue_data = read_cue_sheet(file_path)


                    artifact = {
                        "artefact_hash": calculate_sha256(file_path),

                        "artefact_type": "text/cue-sheet",

                        "attributes": cue_data,

                        "evidence": []
                    }
                    cue_sheets.append(artifact)

                except Exception as e:
                    print(f"Error reading cue sheet " f"{file_path}: {e}")


    return cue_sheets
#==========================

# CompSheet Parsing
def is_comp_section(text):
    """
    Checks whether a row is a section heading.
    """

    return bool(re.match(r"^(?:" r"VERSE" r"|BRIDGE" r"|CHORUS" r"|PRE[- ]?CHORUS" r"|INTRO" r"|OUTRO" r"|REFRAIN" r"|HOOK" r"|INTERLUDE" r"|SOLO" r"|BREAK" r")" 
                         r"(?:\s+.*)?$", text.strip(), re.IGNORECASE))

def read_comp_sheet(file_path):
    """
    Reads a CompSheet PDF.
    """

    song_title = None
    lyrics = []
    take_selections = []
    comp_notes = []

    with pdfplumber.open(file_path) as pdf:
        full_text = ""

        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                full_text += (page_text + "\n")


        song_title = extract_song_title(full_text)

        for page in pdf.pages:
            tables = page.extract_tables()

            for table in tables:
                for row in table:
                    if not row:
                        continue


                    row = [
                        cell.strip()
                        if cell
                        else ""

                        for cell in row
                    ]

                    first_cell = row[0]

                    if not first_cell:
                        continue

                    if is_comp_section(first_cell):
                        lyrics.append(f"[{first_cell}]")

                    else:
                        lyrics.append(first_cell)

                    for column_number, cell in enumerate(row[1:],start=1):
                        if cell:
                            selection = (f"Take {column_number}: " f"{first_cell} " f"-> {cell}")
                            take_selections.append(selection)


    lyrics_text = "\n".join(
        lyrics
    )


    return {
        "document": {
            "song_title": song_title,

            "lyrics": lyrics_text,

            "take_selections": take_selections,

            "comp_notes": comp_notes
        },

        "source_metadata": {
            "original_filename":
                os.path.basename(file_path)
        }
    }

def scan_comps_directory(path):
    """
    Scans for comp-sheet PDFs.
    """
    comp_sheets = []

    for root, folders, files in os.walk(path):
        for file in sorted(files):
            if file.lower().endswith(".pdf"):
                file_path = os.path.join(
                    root,
                    file
                )

                try:
                    comp_data = read_comp_sheet(
                        file_path
                    )

                    artifact = {
                        "artefact_hash":
                            calculate_sha256(
                                file_path
                            ),

                        "artefact_type":
                            "text/comp-sheet",

                        "attributes":
                            comp_data,

                        "evidence": []
                    }
                    comp_sheets.append(artifact)


                except Exception as e:
                    print(f"Error reading comp sheet " f"{file_path}: {e}")


    return comp_sheets
#=========================

#Save JSON to disk
def save_json(data, output_path):
    """
    Saves JSON to disk.
    """

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file: json.dump(data, file, indent=4, ensure_ascii=False)

if __name__ == "__main__":

    # Scan WAV takes
    takes = scan_directory(TAKES_FOLDER)

    # Scan PDF comp sheets
    comps = scan_comps_directory(COMPS_FOLDER)

    # Scan PDF cue sheets
    cues = scan_cues_directory(CUES_FOLDER)

    artefacts = (takes + comps + cues)
    # Save the JSON file
    output = {
        "artefacts": artefacts
    }


    save_json(output, OUTPUT_FILE)

    print("---------------------------")
    print("Evidence bundle created")

    print("Takes found:", len(takes))
    print("Comp sheets found:", len(comps))
    print("Cue sheets found:", len(cues))
    print("Total artefacts:", len(artefacts))
    print("Output:", OUTPUT_FILE)