from ptterm.layout import Terminal
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout


application = Application(
    layout=Layout(
        container=Terminal(),
    ),
    use_alternate_screen=True,
)
application.run()
