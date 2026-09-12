# Testing

Run from the project root.

## First-time setup

``` shell
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x test.sh
```

## Running the tests

``` shell
./test.sh
```
This runs the full pytest suite (`tests/`) in verbose mode.

