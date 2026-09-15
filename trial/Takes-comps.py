import os
import wave
import json
import hashlib
import uuid



# Folder containing all take files (.wav)
TAKES_FOLDER = os.path.join("data", "run_run_run", "takes")          # Can be change later to a different folder if needed

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


def get_wave_data(file_path, artifact_id):
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

            return {
                "id": artifact_id,

                "file": {
                    "path": file_path,
                    "media_type": "audio/wav"
                },

                "hash": {
                    "hash_method": "sha256",
                    "value": calculate_sha256(file_path)
                },

                "type": {
                    "general": "audio",
                    "subtype": "recording",
                    "role": "take"
                },

                "metadata": {
                    "duration_seconds": duration,
                    "sample_rate": sample_rate,
                    "channels": channels
                },

                "provenance": {
                    "c2pa": {
                        "present": False
                    }
                },

                "extension": {
                    "audio": {
                        "rin": {
                            "sessions": [],
                            "contributors": [],
                            "equipment": [],
                            "recording_components": []
                        },

                        "ern": {
                            "contains_ai": None,
                            "ai_contributions": []
                        }
                    },

                   
                    "wave": {
                        "sample_width": sample_width,
                        "bit_depth": bit_depth,
                        "num_frames": num_frames
                    }
                }
            }

    except wave.Error as e:                             #   Error bad :<
        print(f"Error reading wave file: {e}")
        return None


def scan_directory(path):
    """
    Scans the given directory for .wav files and returns their wave data.
    """
    wave_list = []                                      # List to hold wave data dictionaries
    artifact_number = 1
    for root, folders, files in os.walk(path):          # loop through all files in the directory and its subdirectories
        for file in files:                              # Check if the file is a .wav file
            if file.lower().endswith('.wav'):           # Check if the file has a .wav extension
                file_path = os.path.join(root, file)    # Get the full path of the file
                artifact_id = f"take_{artifact_number:03}"
                wave_data = get_wave_data(file_path, artifact_id)
                if wave_data:
                    wave_list.append(wave_data)         # Add the wave data to the list if it's not None
                    artifact_number += 1

    return wave_list

def scan_comps_directory(path):
    """
    Scans the given directory for .pdf files and returns their metadata.
    """
    comp_sheets = []                                    # List to hold comp sheet metadata dictionaries
    artifact_number = 1
    for root, folders, files in os.walk(path):          # loop through all files in the directory and its subdirectories
        for file in files:                              # Check if the file is a .pdf file
            if file.lower().endswith('.pdf'):           # Check if the file has a .pdf extension
                file_path = os.path.join(root, file)    # Get the full path of the file
                artifact = {
                    "id": f"comp_{artifact_number:03}",

                    "file": {
                        "path": file_path,
                        "media_type": "application/pdf"
                    },

                    "hash": {
                        "hash_method": "sha256",
                        "value": calculate_sha256(file_path)
                    },

                    "type": {
                        "general": "document",
                        "subtype": "production-document",
                        "role": "comp-sheet"
                    },

                    "metadata": {
                        "duration_seconds": None,
                        "sample_rate": None,
                        "channels": None
                    },

                    "provenance": {
                        "c2pa": {
                            "present": False
                        }
                    },

                    "extension": {
                        "audio": {
                            "rin": {
                                "sessions": [],
                                "contributors": [],
                                "equipment": [],
                                "recording_components": []
                            },

                            "ern": {
                                "contains_ai": None,
                                "ai_contributions": []
                            }
                        }
                    }
                }

                comp_sheets.append(artifact)

                artifact_number += 1

    return comp_sheets

def create_evidence_bundle(takes, comps):
    """
    Combines all artifacts into the final evidence bundle.
    """

    artifacts = takes + comps

    bundle = {
        "schema_version": "1.0",

        "bundle_id": str(uuid.uuid4()),

        "domain_profile": "audio",

        "final_artifact_id": "",

        "artifacts": artifacts,

        "relationships": []
    }

    return bundle


def save_bundle(bundle, output_path):
    """
    Saves evidence bundle as JSON.
    """

    os.makedirs(
        os.path.dirname(output_path),
        exist_ok=True
    )

    with open(output_path, "w", encoding="utf-8") as file:

        json.dump(
            bundle,
            file,
            indent=4
        )


if __name__ == "__main__":

    # Scan WAV takes
    takes = scan_directory(TAKES_FOLDER)

    # Scan PDF comp sheets
    comps = scan_comps_directory(COMPS_FOLDER)

    # Create final evidence bundle
    bundle = create_evidence_bundle(takes, comps)

    # Save the JSON file
    save_bundle(bundle, OUTPUT_FILE)

    print("---------------------------")
    print("Evidence bundle created")

    print("Takes found:", len(takes))
    print("Comp sheets found:", len(comps))

    print("Output:", OUTPUT_FILE)