PYTHON ?= /usr/local/python/3.9.10/bin/python3.9
VENV := backend/venv/bin/python
NPM ?= npm
CONFIG ?=
config_arg = $(if $(CONFIG),--config "$(CONFIG)")
check_version = "$(1)" -c 'import platform, sys; actual = platform.python_version(); sys.exit(0 if actual == "3.9.10" else "Expected Python 3.9.10, found %s at %s" % (actual, sys.executable))'
.PHONY: all check-python check-venv venv backend frontend executable package-backend test test-backend run route clean
all: executable
check-python:
	@$(call check_version,$(PYTHON))
check-venv: check-python
	@if test -x "$(VENV)"; then $(call check_version,$(VENV)); fi
venv: check-venv
	$(PYTHON) -m venv backend/venv
backend: backend/venv/.installed
backend/venv/.installed: backend/requirements.txt | check-venv
	$(PYTHON) -m venv backend/venv
	$(VENV) -m pip install -r $<
	touch $@
frontend/node_modules/.installed: frontend/package.json frontend/package-lock.json
	cd frontend && $(NPM) ci --include=dev --no-audit --no-fund
	touch $@
frontend: frontend/node_modules/.installed
	cd frontend && $(NPM) run build
executable: frontend
executable package-backend: backend
	$(VENV) -m pip install 'pyinstaller==6.22.3'
	@$(VENV) -c 'import platform, sys, PyInstaller; print("Python %s (%s), PyInstaller %s" % (platform.python_version(), sys.executable, PyInstaller.__version__))'
	cd backend && venv/bin/python -m PyInstaller --clean --noconfirm --onefile --name case-investigator --add-data '../frontend/dist:frontend/dist' run_api.py
test: frontend
test test-backend: backend
	cd backend && venv/bin/python -m unittest discover -s tests -v
run: backend frontend
	$(VENV) backend/run_api.py $(config_arg)
route: check-python
	@$(MAKE) --no-print-directory backend >&2
	@$(VENV) deploy/route.py $(config_arg)
clean:
	rm -rf frontend/dist backend/build backend/dist backend/case-investigator.spec
