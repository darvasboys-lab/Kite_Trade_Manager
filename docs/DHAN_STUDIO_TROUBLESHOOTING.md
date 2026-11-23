## Windows launcher diagnostics

If double-clicking `run_dhan_preview.bat` closes the window, start it from CMD inside
the `DhanPayoffStudio` folder. The launcher now prints Python, package-install
and Streamlit startup milestones, and pauses on errors.

Alternative commands (one line at a time, in Windows CMD):

```bat
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dhan-visual.txt
.venv\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503
```

If port 8503 is occupied by an older instance, use another port such as 8504.

The independent local UI is **offline demo only**, not ResultBot's trading service.
