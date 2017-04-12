# encoding: utf-8
"""
The layout engine. This builds the prompt_toolkit layout.
"""
from __future__ import unicode_literals

from prompt_toolkit.eventloop.defaults import get_event_loop
from prompt_toolkit.filters import to_cli_filter
from prompt_toolkit.layout.containers import Container, Window
from prompt_toolkit.layout.dimension import LayoutDimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import Event
from prompt_toolkit.token import Token

from six.moves import range

import six

from .screen import DEFAULT_TOKEN
from .process import Process



class _UseCopyTokenListProcessor(Processor):
    """
    In order to allow highlighting of the copy region, we use a preprocessed
    list of (Token, text) tuples. This processor returns just that list for the
    given pane.
    """
    def __init__(self, arrangement_pane):
        self.arrangement_pane = arrangement_pane

    def apply_transformation(self, app, document, lineno, source_to_display, tokens):
        tokens = self.arrangement_pane.copy_get_tokens_for_line(lineno)
        return Transformation(tokens[:])

    def invalidation_hash(self, app, document):
        return document.text


from prompt_toolkit.layout.controls import UIControl, UIContent, UIControlKeyBindings
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
import sys

class TerminalControl(UIControl):
    def __init__(self, loop=None, command=['/bin/bash']):
        self.loop = loop or get_event_loop()

        def done_callback(*a, **kw):
            sys.exit(0)   # TODO
            pass

        self.process = Process.from_command(
            self.loop, lambda: self.on_content_changed.fire(),
            command, done_callback, bell_func=None, before_exec_func=None,
            has_priority=None)
        self.process.start()

        self.on_content_changed = Event(self)

    def create_content(self, app, width, height):
        self.process.set_size(width, height)

        if not self.process.screen:
            return UIContent()

        pt_screen = self.process.screen.pt_screen
        data_buffer = pt_screen.data_buffer
        cursor_y = pt_screen.cursor_position.y
        cursor_x = pt_screen.cursor_position.x

        def get_line(number):
            row = data_buffer[number]
            empty = True
            if row:
                max_column = max(row)
                empty = False
            else:
                max_column = 0

            if number == cursor_y:
                max_column = max(max_column, cursor_x)
                empty = False

            if empty:
                return [(Token, ' ')]
            else:
                cells = [row[i] for i in range(max_column + 1)]
                return [(cell.token, cell.char) for cell in cells]

        if data_buffer:
            line_count = max(data_buffer) + 1
        else:
            line_count = 1

        return UIContent(
            get_line, line_count=line_count,
            cursor_position=Point(
                x=pt_screen.cursor_position.x,
                y=pt_screen.cursor_position.y))

    def get_key_bindings(self, app):
        bindings = KeyBindings()

        @bindings.add(Keys.Any)
        @bindings.add(Keys.ControlM)
        @bindings.add(Keys.ControlJ)
        @bindings.add(Keys.ControlC)
        def _(event):
            self.process.write_key(event.key_sequence[0].key)

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            self.process.write_input(event.data, paste=True)

        return UIControlKeyBindings(key_bindings=bindings, modal=False)

    def get_invalidate_events(self):
        yield self.on_content_changed


class Terminal(object):
    def __init__(self, loop=None):
        self.loop = loop or get_event_loop()
        self.container = Window(content=TerminalControl(loop),
                wrap_lines=True)

    def __pt_container__(self):
        return self.container


class Vt100Window(Container):
    """
    Container that holds the VT100 control.
    """
    def __init__(self, process, has_focus):
        self.process = process
        self.has_focus = to_cli_filter(has_focus)

        self.invalidate = Event(self)

    def reset(self):
        pass

    def get_invalidate_events(self):
        yield self.invalidate

    def preferred_width(self, app, max_available_width):
        return LayoutDimension()

    def preferred_height(self, app, width, max_available_height):
        return LayoutDimension()

    def write_to_screen(self, app, screen, mouse_handlers, write_position):
        """
        Write window to screen. This renders the user control, the margins and
        copies everything over to the absolute position at the given screen.
        """
        # Set size of the screen.
        self.process.set_size(write_position.width, write_position.height)

        vertical_scroll = self.process.screen.line_offset

        # Render UserControl.
        temp_screen = self.process.screen.pt_screen

        # Write body to screen.
        self._copy_body(app, temp_screen, screen, write_position, vertical_scroll,
                        write_position.width)

        # Set mouse handlers.
        def mouse_handler(app, mouse_event):
            """ Wrapper around the mouse_handler of the `UIControl` that turns
            absolute coordinates into relative coordinates. """
            position = mouse_event.position

            # Call the mouse handler of the UIControl first.
            self._mouse_handler(
                app, MouseEvent(
                    position=Point(x=position.x - write_position.xpos,
                                   y=position.y - write_position.ypos + vertical_scroll),
                    event_type=mouse_event.event_type))

        mouse_handlers.set_mouse_handler_for_range(
            x_min=write_position.xpos,
            x_max=write_position.xpos + write_position.width,
            y_min=write_position.ypos,
            y_max=write_position.ypos + write_position.height,
            handler=mouse_handler)

        # If reverse video is enabled for the whole screen.
        if self.process.screen.has_reverse_video:
            data_buffer = screen.data_buffer

            for y in range(write_position.ypos, write_position.ypos + write_position.height):
                row = data_buffer[y]

                for x in range(write_position.xpos, write_position.xpos + write_position.width):
                    char = row[x]
                    token = list(char.token or DEFAULT_TOKEN)

                    # The token looks like ('C', *attrs). Replace the value of the reverse flag.
                    if token and token[0] == 'C':
                        token[-1] = not token[-1]  # Invert reverse value.
                        row[x] = Char(char.char, tuple(token))

    def _copy_body(self, app, temp_screen, new_screen, write_position,
                   vertical_scroll, width):
        """
        Copy characters from the temp screen that we got from the `UIControl`
        to the real screen.
        """
        xpos = write_position.xpos
        ypos = write_position.ypos
        height = write_position.height

        temp_buffer = temp_screen.data_buffer
        new_buffer = new_screen.data_buffer
        temp_screen_height = temp_screen.height

        vertical_scroll = self.process.screen.line_offset
        y = 0

        # Now copy the region we need to the real screen.
        for y in range(0, height):
            # We keep local row variables. (Don't look up the row in the dict
            # for each iteration of the nested loop.)
            new_row = new_buffer[y + ypos]

            if y >= temp_screen_height and y >= write_position.height:
                # Break out of for loop when we pass after the last row of the
                # temp screen. (We use the 'y' position for calculation of new
                # screen's height.)
                break
            else:
                temp_row = temp_buffer[y + vertical_scroll]

                # Copy row content, except for transparent tokens.
                # (This is useful in case of floats.)
                for x in range(0, width):
                    new_row[x + xpos] = temp_row[x]

        if self.has_focus(app):
            new_screen.cursor_position = Point(
                y=temp_screen.cursor_position.y + ypos - vertical_scroll,
                x=temp_screen.cursor_position.x + xpos)

            new_screen.show_cursor = temp_screen.show_cursor

        # Update height of the output screen. (new_screen.write_data is not
        # called, so the screen is not aware of its height.)
        new_screen.height = max(new_screen.height, ypos + y + 1)

    def _mouse_handler(self, app, mouse_event):
        """
        Handle mouse events in a pane. A click in a non-active pane will select
        it, one in an active pane, will send the mouse event to the application
        running inside it.
        """
        process = self.process
        x = mouse_event.position.x
        y = mouse_event.position.y

        # The containing Window translates coordinates to the absolute position
        # of the whole screen, but in this case, we need the relative
        # coordinates of the visible area.
        y -= self.process.screen.line_offset

        if not self.has_focus(app):
            # Focus this process when the mouse has been clicked.
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                # XXX: something like      .............................  app.layout.focus(self)
                self.set_focus_cb(app)
        else:
            # Already focussed, send event to application when it requested
            # mouse support.
            if process.screen.sgr_mouse_support_enabled:
                # Xterm SGR mode.
                ev, m = {
                    MouseEventType.MOUSE_DOWN: ('0', 'M'),
                    MouseEventType.MOUSE_UP: ('0', 'm'),
                    MouseEventType.SCROLL_UP: ('64', 'M'),
                    MouseEventType.SCROLL_DOWN: ('65', 'M'),
                }.get(mouse_event.event_type)

                self.process.write_input(
                    '\x1b[<%s;%s;%s%s' % (ev, x + 1, y + 1, m))

            elif process.screen.urxvt_mouse_support_enabled:
                # Urxvt mode.
                ev = {
                    MouseEventType.MOUSE_DOWN: 32,
                    MouseEventType.MOUSE_UP: 35,
                    MouseEventType.SCROLL_UP: 96,
                    MouseEventType.SCROLL_DOWN: 97,
                }.get(mouse_event.event_type)

                self.process.write_input(
                    '\x1b[%s;%s;%sM' % (ev, x + 1, y + 1))

            elif process.screen.mouse_support_enabled:
                # Fall back to old mode.
                if x < 96 and y < 96:
                    ev = {
                            MouseEventType.MOUSE_DOWN: 32,
                            MouseEventType.MOUSE_UP: 35,
                            MouseEventType.SCROLL_UP: 96,
                            MouseEventType.SCROLL_DOWN: 97,
                    }.get(mouse_event.event_type)

                    self.process.write_input('\x1b[M%s%s%s' % (
                        six.unichr(ev),
                        six.unichr(x + 33),
                        six.unichr(y + 33)))

    def walk(self):
        # Only yield self. A window doesn't have children.
        yield self
