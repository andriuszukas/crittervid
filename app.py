"""Old name of the Streamlit app, now src/crittervid/gui/streamlit_app.py, kept so
`streamlit run app.py` works until the package restructure is complete
(docs/decisions/0002). Prefer `python -m crittervid gui`."""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'crittervid', 'gui', 'streamlit_app.py'),
               run_name='__main__')
