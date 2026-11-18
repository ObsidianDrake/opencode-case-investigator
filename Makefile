PYTHON ?= /usr/local/python/3.9.10/bin/python3.9
VENV := backend/venv/bin/python
.PHONY: all venv backend frontend executable test run route clean
all: executable
venv:
	$(PYTHON) -m venv backend/venv
backend: backend/venv/.installed
backend/venv/.installed: backend/requirements.txt
	$(PYTHON) -m venv backend/venv
	$(VENV) -m pip install -r $<
	touch $@
frontend:
	mkdir -p frontend/dist
	cp -R frontend/index.html frontend/assets frontend/dist/
executable: frontend backend
	$(VENV) -m pip install 'pyinstaller==6.16.0'
	cd backend && venv/bin/python -m PyInstaller --noconfirm --onefile --name case-investigator --add-data '../frontend/dist:frontend' run_api.py
test: backend
	cd backend && venv/bin/python -m unittest discover -s tests -v
run: backend
	$(VENV) backend/run_api.py
route:
	@$(PYTHON) deploy/route.py
clean:
	rm -rf frontend/dist backend/build backend/dist backend/case-investigator.spec
