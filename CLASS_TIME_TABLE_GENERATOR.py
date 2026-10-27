"""APMHSS Class Time Table Generator — improved classroom edition.

Run: python CLASS_TIME_TABLE_GENERATOR.py
Install: python -m pip install pillow python-docx reportlab openpyxl
The local SQLite database is created under the user's home folder.
"""
from __future__ import annotations
from schedule_layout import make_periods, columns as period_columns, subject_counts
import calendar
import os
import random
import secrets
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog, colorchooser

from timetable_core import (Store,Scheduler,Slot,TimetableError,DAYS,GRADES,SCHOOL,
                            PERIODS,group,school_days,UNIT_GRADES,unit_for_grade,weekly_capacity,section_display)
from edification_core import Edification
from timetable_export import EXPORTERS

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
MAROON='#7A0019';GOLD='#D4AF37';NAVY='#22334C';LIGHT='#EFF3F8';TEXT='#27384C'
CREDITS = (
    ('ADMIN (FOR ALL)', 'P. SAI SACHIN'),
    ('CORRESPONDENT', 'SRIDEVI BANGARU'),
    ('PRINCIPAL', 'ANANDRAJ'),
    ('VICE PRINCIPAL', 'ANNANDRAJ'),
    ('PROJECT GUIDE', 'D. JAYANTHI. M.C.A.,B.Ed.\nPGT- Department of Computer Science'),
)



def text_entry(parent,label,show=None,default=''):
    ttk.Label(parent,text=label).pack(anchor='w',pady=(10,2))
    e=ttk.Entry(parent,width=37,show=show)
    e.pack(fill='x');e.insert(0,default)
    return e

class App:
    def __init__(self,root):
        self.root=root
        self.root.title('APMHSS | Class Time Table Generator')
        try:
            self.root.iconbitmap(str(BASE / 'Adhiparasakthi_Time_Table_Icon.ico'))
        except tk.TclError:
            pass
        self.root.geometry('1300x810')
        self.root.minsize(1040,670)
        self.root.configure(bg=LIGHT)
        self.auth_store=Store()  # Accounts and login always use the class database.
        self.store=self.auth_store
        self.mode='class'
        self.revision_store=None
        self.scheduler=Scheduler(self.store)
        self.edification=Edification(self.auth_store)
        self.user='';self.role='viewer';self.login_failures=0
        self.slots={};self.dirty=False;self.loaded_context=None
        self.colors={}
        self.table_zoom=1.12  # Can be adjusted and saved separately for each mode.
        self._table_resize_job=None
        self._welcome_job=None
        self._welcome_overlay=None
        self._login_in_progress=False
        self._configure_style()
        self.root.protocol('WM_DELETE_WINDOW',self.exit_app)
        self.login_screen()
        if not self.store.has_users():
            self.root.after(100,self.first_run)

    def _configure_style(self):
        style=ttk.Style(self.root)
        try: style.theme_use('clam')
        except tk.TclError: pass
        style.configure('TButton',font=('Segoe UI',10),padding=(10,7))
        style.configure('TCombobox',padding=4)
        style.configure('TLabel',font=('Segoe UI',10))
        style.configure('Treeview',rowheight=27)
        style.configure('Treeview.Heading',font=('Segoe UI',9,'bold'))

    def reset_root(self):
        for w in self.root.winfo_children(): w.destroy()

    def image(self,name,size):
        try:
            from PIL import Image,ImageTk
            img=Image.open(BASE/name)
            img.thumbnail(size)
            return ImageTk.PhotoImage(img)
        except (ImportError,OSError):
            return None

    def _make_original_login_background(self, width, height):
        """Re-create the full-screen school-photo background from the original app.

        Source image is the *same* school photo extracted from the original program.
        The panel is softly blurred and darkened, exactly as in the earlier styling.
        """
        from PIL import Image, ImageFilter, ImageTk

        width, height = max(int(width), 1), max(int(height), 1)
        if not hasattr(self, '_original_login_source'):
            with Image.open(BASE / 'school_photo.jpg') as source:
                self._original_login_source = source.convert('RGB').copy()
        src = self._original_login_source
        scale = max(width / src.width, height / src.height)
        nw = max(width, round(src.width * scale))
        nh = max(height, round(src.height * scale))
        background = src.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - width) // 2
        top = int((nh - height) * .4)
        background = background.crop((left, top, left + width, top + height))
        if scale > 1.3:
            background = background.filter(ImageFilter.UnsharpMask(radius=2, percent=90, threshold=2))

        panel_width = min(440, max(300, width - 24))
        panel_height = min(640, max(370, height - 100))
        panel_x = (width - panel_width) // 2
        panel_y = max(8, (height - panel_height) // 2 - 22)
        box = (panel_x, panel_y, panel_x + panel_width, panel_y + panel_height)
        region = background.crop(box).filter(ImageFilter.GaussianBlur(3))
        tint = Image.new('RGB', region.size, (6, 18, 32))
        background.paste(Image.blend(region, tint, 0.46), box)
        return ImageTk.PhotoImage(background), (panel_x, panel_y, panel_width, panel_height)

    def _original_login_logo(self):
        """Use the original centrally cropped, feather-masked school logo."""
        try:
            from PIL import Image, ImageTk
            with Image.open(BASE / 'school_logo.png') as image:
                image = image.convert('RGBA')
                if image.width >= 393 and image.height >= 380:
                    image = image.crop((43, 30, 393, 380))
                mask = Image.radial_gradient('L').resize(image.size)
                mask = mask.point(lambda value: 255 if value < 176 else 0)
                image.putalpha(mask)
                image = image.resize((100, 100), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(image)
        except (ImportError, OSError, ValueError):
            return self.image('school_logo.png', (100, 100))

    def _resize_original_login(self, event=None):
        """Throttle costly photo resizing while the window is being resized."""
        old = getattr(self, '_login_resize_job', None)
        if old is not None:
            try: self.root.after_cancel(old)
            except tk.TclError: pass
        self._login_resize_job = self.root.after(90, self._layout_original_login)

    def _layout_original_login(self):
        self._login_resize_job = None
        cv = getattr(self, '_login_canvas', None)
        if cv is None or not cv.winfo_exists():
            return
        width = cv.winfo_width()
        height = cv.winfo_height()
        if width < 100 or height < 100:
            return
        try:
            self._login_bg_photo, panel = self._make_original_login_background(width, height)
            cv.itemconfigure(self._login_bg_item, image=self._login_bg_photo)
        except (ImportError, OSError, ValueError):
            panel = ((width - 440) // 2, (height - 640) // 2, 440, 640)
        x, y, pw, ph = panel
        scale = min(1., ph / 640.)
        center = x + pw // 2
        cv.coords(self._login_bg_item, 0, 0)
        cv.coords(self._login_border, x, y, x + pw, y + ph)
        if self._login_logo_item is not None:
            cv.coords(self._login_logo_item, center, y + 72 * scale)
        for item, offset in self._login_text_items:
            cv.coords(item, center, y + offset * scale)
        field_left = x + 40
        field_width = pw - 80
        cv.coords(self._login_username_label, field_left, y + 372 * scale)
        cv.coords(self._login_user_window, center, y + 402 * scale)
        cv.itemconfigure(self._login_user_window, width=field_width)
        cv.coords(self._login_password_label, field_left, y + 448 * scale)
        cv.coords(self._login_pass_window, center, y + 478 * scale)
        cv.itemconfigure(self._login_pass_window, width=field_width)
        cv.coords(self._login_show_window, center, y + 514 * scale)
        cv.coords(self._login_button_window, center, y + 552 * scale)
        cv.coords(self._login_caps_item, center, y + 584 * scale)
        cv.itemconfigure(self._login_button_window, width=field_width)
        cv.coords(self._login_setup_note, center, y + 613 * scale)
        cv.coords(self._login_motto_shadow, width // 2 + 2, height - 28)
        cv.coords(self._login_motto, width // 2, height - 30)

    def login_screen(self):
        """Original APMHSS full-photo login design with improved secure accounts."""
        self.reset_root()
        cv = tk.Canvas(self.root, bg='#07111F', highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        self._login_canvas = cv
        self._login_bg_photo = None  # Hold a reference; otherwise Tk hides the image.
        self._login_resize_job = None
        self._login_bg_item = cv.create_image(0, 0, anchor='nw')
        self._login_border = cv.create_rectangle(0, 0, 1, 1, outline='#00D9FF', width=2)

        self.logo = self._original_login_logo()
        self._login_logo_item = cv.create_image(0, 0, image=self.logo) if self.logo else None
        headings = [
            (150, 'OM SAKTHI', ('Segoe UI', 17, 'bold'), '#FFD84D', 0),
            (196, 'ADHIPARASAKTHI MATRICULATION\nHIGHER SECONDARY SCHOOL',
             ('Segoe UI', 11, 'bold'), 'white', 340),
            (238, 'MELMARUVATHUR', ('Segoe UI', 9, 'bold'), '#7CEEFF', 0),
            (288, 'SIGN IN', ('Segoe UI', 30, 'bold'), 'white', 0),
            (328, 'LOGIN TO CONTINUE', ('Segoe UI', 10, 'bold'), '#00D9FF', 0),
        ]
        self._login_text_items = [(cv.create_text(0, 0, text=label, font=font,
                                                 fill=color, justify='center', width=text_width), offset)
                                  for offset, label, font, color, text_width in headings]
        self._login_username_label = cv.create_text(0, 0, text='USER NAME',
                                                    font=('Segoe UI', 9, 'bold'), fill='#DDEAF5', anchor='w')
        self._login_password_label = cv.create_text(0, 0, text='PASSWORD',
                                                    font=('Segoe UI', 9, 'bold'), fill='#DDEAF5', anchor='w')
        self.user_field = ttk.Entry(cv, font=('Segoe UI', 12))
        # No fixed username: the administrator chooses it during first setup.
        self.pass_field = ttk.Entry(cv, font=('Segoe UI', 12), show='*')
        self.pass_field.bind('<Return>', lambda event: self.sign_in())
        self.show_password_var=tk.BooleanVar(value=False)
        show_pw=tk.Checkbutton(cv,text='Show password',variable=self.show_password_var,
            command=lambda:self.pass_field.configure(show='' if self.show_password_var.get() else '*'),
            bg='#101F30',fg='white',selectcolor='#101F30',activebackground='#101F30',
            activeforeground='white',font=('Segoe UI',9))
        self._login_show_window=cv.create_window(0,0,window=show_pw)
        self._login_caps_item=cv.create_text(0,0,text='',font=('Segoe UI',9,'bold'),fill='#FFD85C')
        self._update_caps_indicator()
        login_button = tk.Button(cv, text='LOGIN SECURELY', bg='#00CFE8', fg='#06111D',
                                 activebackground='#71F2FF', activeforeground='#06111D',
                                 font=('Segoe UI', 10, 'bold'), relief='flat', bd=0,
                                 cursor='hand2', command=self.sign_in)
        self._login_user_window = cv.create_window(0, 0, window=self.user_field, height=36)
        self._login_pass_window = cv.create_window(0, 0, window=self.pass_field, height=36)
        self._login_button_window = cv.create_window(0, 0, window=login_button, height=40)
        self._login_setup_note = cv.create_text(0, 0,
                                                text=('Sign in with your saved administrator password' if self.store.has_users()
                                                      else 'First use: create an administrator password'),
                                                font=('Segoe UI', 9), fill='#DDEAF5')
        self._login_motto_shadow = cv.create_text(0, 0, text='LEARN  •  GROW  •  ACHIEVE',
                                                  font=('Segoe UI', 18, 'bold'), fill='black')
        self._login_motto = cv.create_text(0, 0, text='LEARN  •  GROW  •  ACHIEVE',
                                           font=('Segoe UI', 18, 'bold'), fill='white')
        # Redraw when window size changes, without losing the live input fields.
        cv.bind('<Configure>', self._resize_original_login)
        self.root.update_idletasks()
        self._layout_original_login()
        self.user_field.focus_set()

    def _update_caps_indicator(self):
        cv=getattr(self,'_login_canvas',None)
        if cv is None or not cv.winfo_exists():return
        active=False
        if sys.platform=='win32':
            try:
                import ctypes
                active=bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
            except (AttributeError,OSError):pass
        cv.itemconfigure(self._login_caps_item,
                         text='CAPS LOCK IS ON' if active else '')
        self.root.after(500,self._update_caps_indicator)

    def first_run(self):
        if self.store.has_users():return
        username=simpledialog.askstring('First-time setup','Choose your administrator USERNAME (3–32 letters/digits):',
                                        initialvalue='',parent=self.root)
        if username is None:self.root.destroy();return
        pw=simpledialog.askstring('First-time setup','Create ADMIN password (8+ characters, letters and numbers):',show='*',parent=self.root)
        if pw is None: self.root.destroy();return
        confirm=simpledialog.askstring('Confirm password','Enter the new admin password again:',show='*',parent=self.root)
        if confirm is None: self.root.destroy();return
        if pw!=confirm:
            messagebox.showerror('Password mismatch','Passwords did not match. Try again.')
            self.root.after(150,self.first_run);return
        try: self.store.add_user(username,pw,'admin')
        except (TimetableError,Exception) as err:
            messagebox.showerror('Password requirements',str(err));self.root.after(150,self.first_run);return
        messagebox.showinfo('Administrator created',f'Admin username: {username.strip().lower()}\nUse your new password to sign in.')

    def sign_in(self):
        username=self.user_field.get().strip().lower()
        password=self.pass_field.get()
        if not self.auth_store.has_users():
            messagebox.showwarning('Setup required','Create the admin account first.');self.first_run();return
        if self.login_failures>=5:
            messagebox.showwarning('Login limited','Too many wrong attempts. Close and reopen the application.');return
        role=self.auth_store.login(username,password)
        self.pass_field.delete(0,'end')
        if not role:
            self.login_failures+=1
            self.auth_store.log(username or '(blank)','Failed login')
            messagebox.showerror('Login failed','Incorrect username or password.')
            return
        self.user=username;self.role=role
        self.auth_store.log(username,'Signed in')
        self.show_welcome_loading(username)

    def show_welcome_loading(self, username):
        """Animated school-branded welcome + loading screen after a valid login.

        Non-blocking Tkinter after() animation; does not freeze the interface.
        """
        if self._login_in_progress:
            return
        self._login_in_progress=True
        overlay=tk.Frame(self.root,bg='#07111F')
        overlay.place(x=0,y=0,relwidth=1,relheight=1)
        overlay.lift()
        self._welcome_overlay=overlay
        hour=datetime.now().hour
        greeting='Good Morning' if hour < 12 else 'Good Afternoon' if hour < 17 else 'Good Evening'
        quotes=[
            'Great teachers plan today to inspire tomorrow.',
            'A well-planned timetable is the first step to a successful day.',
            'LEARN  •  GROW  •  ACHIEVE',
            'Every period is a chance to change a life.',
            'Discipline today, success tomorrow.',
        ]
        tk.Label(overlay,text=f'✦  {greeting}, {username.title()}!  ✦',
                 font=('Segoe UI',27,'bold'),bg='#07111F',fg='#FFD84D').place(relx=.5,rely=.095,anchor='center')
        tk.Label(overlay,text=random.choice(quotes),font=('Segoe UI',13,'italic'),
                 bg='#07111F',fg='#DDEAF5').place(relx=.5,rely=.165,anchor='center')
        logo=self.image('school_logo.png',(310,310))
        if logo:
            emblem=tk.Label(overlay,image=logo,bg='#07111F')
            emblem.image=logo
        else:
            emblem=tk.Label(overlay,text='OM SAKTHI',font=('Segoe UI',38,'bold'),
                            bg='#07111F',fg='#FFD84D')
        emblem.place(relx=.5,rely=.43,anchor='center')
        tk.Label(overlay,text='OM SAKTHI',font=('Segoe UI',21,'bold'),
                 bg='#07111F',fg='#FFD84D').place(relx=.5,rely=.67,anchor='center')
        tk.Label(overlay,text=SCHOOL,font=('Segoe UI',15,'bold'),
                 bg='#07111F',fg='white').place(relx=.5,rely=.725,anchor='center')
        status=tk.Label(overlay,text='WELCOME TO THE SCHOOL WORKSPACE',
                        font=('Segoe UI',12,'bold'),bg='#07111F',fg='#7CEEFF')
        status.place(relx=.5,rely=.79,anchor='center')
        bar=tk.Canvas(overlay,width=440,height=14,bg='#172B3D',highlightthickness=0,bd=0)
        bar.place(relx=.5,rely=.85,anchor='center')
        fill=bar.create_rectangle(0,0,0,14,fill='#00D9FF',outline='')
        count={'value':0}

        def animate():
            if not overlay.winfo_exists():
                self._welcome_job=None
                return
            count['value']+=1
            percent=min(100,count['value']*4)
            if percent <= 24:
                message='WELCOME!  PREPARING YOUR WORKSPACE'
            elif percent < 100:
                message=f'LOADING SCHOOL WORKSPACE  •  {percent}%'
            else:
                message='READY  •  100%'
            status.configure(text=message)
            bar.coords(fill,0,0,round(440*percent/100),14)
            if percent < 100:
                self._welcome_job=self.root.after(80,animate)
            else:
                self._welcome_job=self.root.after(320,finish)

        def finish():
            self._welcome_job=None
            if overlay.winfo_exists(): overlay.destroy()
            self._welcome_overlay=None
            self._login_in_progress=False
            self.mode_selection_screen()

        animate()

    @staticmethod
    def _credit_key(index, field):
        return f'leadership_{index}_{field}'

    def credit_details(self, index):
        """Editable school credits are stored in the accounts SQLite database."""
        default_title, default = CREDITS[index]
        title = self.auth_store.get_setting(self._credit_key(index, 'title'), default_title)
        first, _, description = default.partition('\n')
        name = self.auth_store.get_setting(self._credit_key(index, 'name'), first)
        description = self.auth_store.get_setting(self._credit_key(index, 'description'), description)
        return title, name, description

    def update_credit(self, index, name, description, title=None):
        if self.role != 'admin':
            raise TimetableError('Only an administrator may edit the school leadership details.')
        name, description = name.strip(), description.strip()
        if not name or len(name) > 150 or len(description) > 500:
            raise TimetableError('Name must be 1–150 characters; description up to 500 characters.')
        if title is not None:
            title = str(title).strip()
            if not title or len(title) > 80:
                raise TimetableError('Leadership label must be 1–80 characters.')
            self.auth_store.set_setting(self._credit_key(index, 'title'), title)
        self.auth_store.set_setting(self._credit_key(index, 'name'), name)
        self.auth_store.set_setting(self._credit_key(index, 'description'), description)
        self.auth_store.log(self.user, f'Updated school leadership entry {index + 1}')

    def edit_credit(self, index):
        if not self.require_admin():
            return
        title, current_name, current_description = self.credit_details(index)
        win = tk.Toplevel(self.root)
        win.title('Edit School Leadership • ' + title)
        win.geometry('540x420')
        win.transient(self.root)
        win.grab_set()
        pane = ttk.Frame(win, padding=18)
        pane.pack(fill='both', expand=True)
        ttk.Label(pane, text='EDIT ' + title, font=('Segoe UI', 14, 'bold')).pack(anchor='w', pady=(0, 12))
        title_entry = text_entry(pane, 'Role / Leadership Label (editable)', default=title)
        name_entry = text_entry(pane, 'Name', default=current_name)
        ttk.Label(pane, text='Description / Qualifications (optional)').pack(anchor='w', pady=(12, 4))
        description_entry = tk.Text(pane, height=4, wrap='word')
        description_entry.pack(fill='both', expand=True)
        description_entry.insert('1.0', current_description)
        def apply():
            try:
                self.update_credit(index, name_entry.get(), description_entry.get('1.0', 'end-1c'),
                                   title=title_entry.get())
            except TimetableError as error:
                messagebox.showerror('Invalid details', str(error), parent=win)
                return
            win.destroy()
            self.mode_selection_screen()
        ttk.Button(pane, text='Save Name & Description', command=apply).pack(fill='x', pady=(12, 0))

    def _credits_panel(self, parent, *, dark=False):
        """Leadership details; administrators double-click a row to edit."""
        bg = '#16283E' if dark else '#FFFFFF'
        fg = '#F6F7FC' if dark else NAVY
        sub = '#E9C66B' if dark else MAROON
        panel = tk.Frame(parent, bg=bg, padx=16, pady=10, highlightbackground=GOLD,
                         highlightthickness=1)
        for index in range(len(CREDITS)):
            title, name, description = self.credit_details(index)
            row = tk.Frame(panel, bg=bg)
            row.pack(fill='x', pady=3)
            label = tk.Label(row, text=title + ':', font=('Segoe UI', 9, 'bold'), bg=bg,
                             fg=sub, width=19, anchor='w')
            label.pack(side='left')
            value = tk.Label(row, text=name + ('\n' + description if description else ''),
                             font=('Segoe UI', 9, 'bold'), bg=bg, fg=fg,
                             justify='left', anchor='w')
            value.pack(side='left', fill='x', expand=True)
            for widget in (row, label, value):
                widget.bind('<Double-Button-1>', lambda event, i=index: self.edit_credit(i))
                if self.role == 'admin':
                    widget.configure(cursor='hand2')
        if self.role == 'admin':
            tk.Label(panel, text='Admin: double-click a role, name or description to edit all three. Changes save automatically.',
                     bg=bg, fg=sub, font=('Segoe UI', 9, 'italic')).pack(anchor='w', pady=(8, 0))
        return panel

    def mode_selection_screen(self):
        """School-branded, colorful landing screen for three independent modes."""
        self.reset_root()
        self.root.configure(bg='#EAF0F9')
        header=tk.Frame(self.root,bg='#10243E',pady=14)
        header.pack(fill='x')
        logo=self.image('school_logo.png',(94,94))
        if logo:
            lab=tk.Label(header,image=logo,bg='#10243E');lab.image=logo
            lab.pack(side='left',padx=(30,20))
        headings=tk.Frame(header,bg='#10243E');headings.pack(side='left',fill='x',expand=True)
        tk.Label(headings,text='OM SAKTHI  •  ADHIPARASAKTHI',
                 bg='#10243E',fg='#FFD36A',font=('Segoe UI',16,'bold')).pack(anchor='w')
        tk.Label(headings,text='MATRICULATION HIGHER SECONDARY SCHOOL  |  MELMARUVATHUR',
                 bg='#10243E',fg='white',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=5)
        tk.Label(header,text=f'{self.user}  ({self.role.upper()})',bg='#10243E',
                 fg='white',font=('Segoe UI',10,'bold')).pack(side='right',padx=26)
        tk.Label(self.root,text='CHOOSE YOUR TIMETABLE',bg='#EAF0F9',fg=NAVY,
                 font=('Segoe UI',25,'bold')).pack(pady=(22,5))
        tk.Label(self.root,text='Select a timetable to manage or view your assigned subjects',
                 bg='#EAF0F9',fg='#566780',font=('Segoe UI',11)).pack(pady=(0,14))
        cards=tk.Frame(self.root,bg='#EAF0F9');cards.pack(fill='x',padx=32)
        modes=[('CLASS TIMETABLE','CLASS','Daily lessons and period allocation',
                '#156AAB','#EAF6FF','📚'),
               ('REVISION TIMETABLE','REVISION','Revision schedule • original layout',
                '#7B3CB0','#F4ECFF','✏'),
               ('EDIFICATION TIMETABLE','EDIFICATION','Morning tests • 8:00–8:45 AM',
                '#168572','#E7F8F2','📅')]
        for title,mode,description,color,tint,symbol in modes:
            frame=tk.Frame(cards,bg='white',highlightbackground='#C8D6E6',highlightthickness=1)
            frame.pack(side='left',fill='both',expand=True,padx=9)
            tk.Frame(frame,bg=color,height=9).pack(fill='x')
            tk.Label(frame,text=symbol,bg='white',fg=color,font=('Segoe UI Symbol',30)).pack(pady=(12,2))
            tk.Label(frame,text=title,bg='white',fg=NAVY,font=('Segoe UI',12,'bold')).pack(pady=(2,4))
            tk.Label(frame,text=description,bg='white',fg='#52647C',
                     font=('Segoe UI',10),wraplength=240).pack(pady=(0,14))
            tk.Button(frame,text='OPEN '+mode,bg=color,fg='white',activebackground=tint,
                      font=('Segoe UI',11,'bold'),relief='flat',cursor='hand2',
                      command=lambda m=mode.lower():self.open_mode(m)).pack(fill='x',padx=18,pady=(0,18),ipady=7)
        credits=self._credits_panel(self.root)
        credits.pack(anchor='w',fill='x',padx=(41,240),pady=(22,10))
        tk.Button(self.root,text='LOG OUT',command=self.logout,relief='flat',
                  bg='#DDE5EF',fg=NAVY,font=('Segoe UI',10,'bold'),
                  padx=26,pady=7).pack(pady=(3,14))

    def open_mode(self, mode):
        if mode not in ('class','revision','edification'):
            raise ValueError('Unknown timetable mode')
        if mode=='edification':
            self.mode=mode
            self.store=self.auth_store
            self.edification_screen()
            return
        if mode=='revision':
            if self.revision_store is None:
                revision_path=self.auth_store.path.with_name('revision_timetable.sqlite3')
                if revision_path.exists():
                    self.revision_store=Store(path=revision_path,kind='revision')
                else:
                    bundled_revision=BASE / 'data' / 'revision_timetable.sqlite3'
                    if bundled_revision.is_file():
                        # On a new computer, restore the revision snapshot if supplied.
                        import shutil
                        shutil.copy2(bundled_revision,revision_path)
                        self.revision_store=Store(path=revision_path,kind='revision')
                    else:
                        # Start a separate revision database from the class settings,
                        # but do not copy any regular class schedules.
                        self.auth_store.backup(revision_path)
                        initial=None
                        try:
                            initial=Store(path=revision_path,kind='revision')
                            with initial.db:
                                initial.db.execute('DELETE FROM schedules')
                                # Drop copied class quotas only if they exceed the
                                # unchanged revision timetable's smaller capacity.
                                rows=initial.db.execute('SELECT grade,section FROM weekly_plan_enabled').fetchall()
                                for row in rows:
                                    g,sec=row['grade'],row['section']
                                    if sum(initial.weekly_plan(g,sec).values()) > initial.capacity(g,sec):
                                        initial.db.execute('DELETE FROM weekly_subject_periods WHERE grade=? AND section=?',(g,sec))
                                        initial.db.execute('DELETE FROM weekly_plan_enabled WHERE grade=? AND section=?',(g,sec))
                            self.revision_store=initial
                        except Exception:
                            if initial is not None:initial.close()
                            revision_path.unlink(missing_ok=True)
                            raise
            self.store=self.revision_store
        else:
            self.store=self.auth_store
        self.mode=mode
        try:
            self.table_zoom=max(.85,min(1.65,float(self.auth_store.get_setting('table_zoom_'+mode,'1.12'))))
        except (ValueError,TypeError):self.table_zoom=1.12
        self.scheduler=Scheduler(self.store)
        self.slots={};self.dirty=False;self.loaded_context=None
        self.main_screen()

    def edification_screen(self):
        from edification_ui import show_edification
        show_edification(self)

    def granted_subjects(self,grade,section):
        if self.role=='admin':return None
        return {subject for _,_,subject in self.auth_store.grants(self.user,grade,section)}

    def allowed_grades(self):
        if self.role=='admin':return list(GRADES)
        return [g for g in GRADES if any(x[0]==g for x in self.auth_store.grants(self.user))]

    def allowed_sections(self,grade):
        sections=self.store.sections(grade)
        if self.role=='admin':return sections
        return [sec for sec in sections if self.granted_subjects(grade,sec)]

    def saved_assigned_contexts(self):
        """Only saved contexts containing this account's subject assignments."""
        if self.role=='admin':return []
        return self.store.contexts_for_subjects(self.auth_store.grants(self.user))

    def _default_teacher_context(self, grade, section):
        # Use the selected school week if the main timetable controls exist;
        # otherwise use the same defaults shown to a new login.
        if getattr(self, 'mode', '') in ('class', 'revision') and hasattr(self, 'year_var'):
            return (self.year_var.get(), self.month_var.get(), self.week_var.get(), grade, section)
        today = datetime.now()
        year = f'{today.year}-{today.year + 1}' if today.month >= 6 else f'{today.year - 1}-{today.year}'
        return (year, calendar.month_name[today.month], 'Week 1', grade, section)

    def _ensure_teacher_context(self):
        """An authorized login can view a generated, saved subject timetable.

        Never overwrite a previously saved timetable. This only fills in a
        completely missing week using the school's existing subject plan.
        """
        if self.role == 'admin' or self.mode == 'edification':
            return []
        issues = []
        for grade, section, subject in self.auth_store.grants(self.user):
            if self.store.contexts_for_subjects([(grade, section, subject)]):
                continue
            context = self._default_teacher_context(grade, section)
            ok, explanation = self.store.ensure_assigned_schedule(context, subject)
            if not ok:
                issues.append(f'{grade}-{section} / {subject}: {explanation}')
        return issues

    def find_my_subject(self, *, initial=False):
        """Jump to a saved week with this teacher's subject, if one exists."""
        if self.role=='admin':return False
        contexts=self.saved_assigned_contexts()
        issues=[]
        if not contexts:
            issues=self._ensure_teacher_context()
            contexts=self.saved_assigned_contexts()
        if not contexts:
            if not initial:
                details='\n'.join(issues[:5]) if issues else 'No subject permission or matching scheduled lessons were found.'
                messagebox.showinfo('Assigned timetable not available',
                    'Your subject is assigned, but no matching timetable could be opened.\n\n'
                    + details + '\n\nClass and Revision schedules are saved separately.')
            return False
        ctx=contexts[0]
        self.cbs['Academic Year']['values']=sorted(set(self.cbs['Academic Year']['values']) | {c[0] for c in contexts})
        self.year_var.set(ctx[0]);self.month_var.set(ctx[1]);self.week_var.set(ctx[2])
        self.grade_var.set(ctx[3]);self.section_var.set(ctx[4])
        self.selection_change(force=True)
        if not initial:
            messagebox.showinfo('My Subject',
                f'Opened {ctx[3]}-{section_display(ctx[3],ctx[4])}, {ctx[2]}, {ctx[1]} {ctx[0]}.\n'
                'Only your assigned subject lessons are visible.')
        return True

    def require_admin(self):
        if self.role=='admin':return True
        messagebox.showwarning('Administrator only','Only the administrator can change the overall timetable or accounts.')
        return False

    def back_to_mode_selection(self):
        if self.guard_unsaved():
            self.slots={};self.dirty=False;self.loaded_context=None
            self.mode_selection_screen()

    def _maximize_main_window(self):
        """Open the timetable in the largest practical desktop window."""
        try:
            self.root.state('zoomed')  # Windows
        except tk.TclError:
            try: self.root.attributes('-zoomed',True)  # Linux window managers
            except tk.TclError:
                w,h=self.root.winfo_screenwidth(),self.root.winfo_screenheight()
                self.root.geometry(f'{w}x{h-45}+0+0')

    def _resize_table_canvas(self,event=None):
        if self._table_resize_job is not None:
            try:self.root.after_cancel(self._table_resize_job)
            except tk.TclError:pass
        self._table_resize_job=self.root.after(65,self._fit_grid_to_canvas)

    def _fit_grid_to_canvas(self):
        self._table_resize_job=None
        if not hasattr(self,'canvas') or not self.canvas.winfo_exists():return
        grade=self.grade_var.get()
        n=len(period_columns(self.store.layout(grade,self.section_var.get())[1]))
        day_width=round(136*self.table_zoom)
        period_width=round(150*self.table_zoom)
        target_width=max(self.canvas.winfo_width(),day_width+n*period_width)
        target_height=max(self.canvas.winfo_height(),round(82*self.table_zoom)+len(self.store.layout(grade,self.section_var.get())[0])*round(98*self.table_zoom))
        self.canvas.itemconfigure(self.canvas_window,width=target_width,height=target_height)
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        available_period=max(period_width,(target_width-day_width)//n)
        for cell in getattr(self,'_table_period_cells',[]):
            if cell.winfo_exists():cell.configure(wraplength=max(90,available_period-20))

    def change_table_zoom(self,step):
        self.table_zoom=round(min(1.65,max(.85,self.table_zoom+step)),2)
        self.auth_store.set_setting('table_zoom_'+self.mode,self.table_zoom)
        if hasattr(self,'zoom_display') and self.zoom_display.winfo_exists():
            self.zoom_display.configure(text=f'{round(self.table_zoom*100)}% • Auto-saved')
        self.draw()

    def fit_table_width(self):
        if not hasattr(self,'canvas') or not self.canvas.winfo_exists():return
        n=len(period_columns(self.store.layout(self.grade_var.get(),self.section_var.get())[1]))
        available=max(600,self.canvas.winfo_width())
        self.table_zoom=round(min(1.45,max(.85,(available-140)/(150*n))),2)
        self.draw()

    def can_edit(self): return self.role in ('admin','editor')
    def require_edit(self):
        if self.can_edit() and (self.role=='admin' or
            (hasattr(self,'grade_var') and self.granted_subjects(self.grade_var.get(),self.section_var.get()))):
            return True
        messagebox.showwarning('Read-only','Your user role does not allow editing.')
        return False

    def main_screen(self):
        self._maximize_main_window()
        self.reset_root()
        header=tk.Frame(self.root,bg=MAROON,pady=8)
        header.pack(fill='x')
        self.small_logo=self.image('school_logo.png',(58,58))
        if self.small_logo: tk.Label(header,image=self.small_logo,bg=MAROON).pack(side='left',padx=12)
        text=tk.Frame(header,bg=MAROON);text.pack(side='left')
        tk.Label(text,text='OM SAKTHI  |  '+SCHOOL,font=('Segoe UI',13,'bold'),fg='white',bg=MAROON).pack(anchor='w')
        tk.Label(text,text=f'{self.mode.upper()} TIME TABLE GENERATOR • MELMARUVATHUR',font=('Segoe UI',10),fg='#F1D394',bg=MAROON).pack(anchor='w')
        tk.Label(header,text=f'{self.user} ({self.role})',bg=MAROON,fg='white').pack(side='right',padx=18)

        controls=tk.Frame(self.root,bg='white',padx=10,pady=9)
        controls.pack(fill='x',padx=10,pady=(10,4))
        self.year_var=tk.StringVar(value='2026-2027')
        self.month_var=tk.StringVar(value=calendar.month_name[datetime.now().month])
        self.week_var=tk.StringVar(value='Week 1')
        grades=self.allowed_grades()
        if not grades:
            self.reset_root()
            ttk.Label(self.root,text='No subjects assigned to this account. Ask your administrator to grant access.',
                      font=('Segoe UI',13)).pack(pady=40)
            ttk.Button(self.root,text='Choose Timetable',command=self.mode_selection_screen).pack(pady=10)
            ttk.Button(self.root,text='Logout',command=self.logout).pack(pady=10)
            return
        initial_grade='8' if '8' in grades else grades[0]
        self.unit_var=tk.StringVar(value=unit_for_grade(initial_grade))
        self.grade_var=tk.StringVar(value=initial_grade)
        first_sections=self.allowed_sections(initial_grade)
        self.section_var=tk.StringVar(value=first_sections[0] if first_sections else '')
        self.students_var=tk.StringVar(value='40')
        self.filter_var=tk.StringVar(value='All Subjects')
        fields=[('Academic Year',self.year_var,['2025-2026','2026-2027','2027-2028']),
                ('Month',self.month_var,list(calendar.month_name)[1:]),
                ('Week',self.week_var,[f'Week {i}' for i in range(1,6)]),
                ('Unit',self.unit_var,list(UNIT_GRADES)),
                ('Class',self.grade_var,[g for g in UNIT_GRADES[self.unit_var.get()] if g in grades]),
                ('Section',self.section_var,self.allowed_sections(initial_grade))]
        self.cbs={}
        for col,(label,var,values) in enumerate(fields):
            tk.Label(controls,text=label,bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=col,sticky='w',padx=5)
            cb=ttk.Combobox(controls,textvariable=var,values=values,state='readonly',width=17 if col!=3 else 9)
            cb.grid(row=1,column=col,sticky='ew',padx=5,pady=4)
            cb.bind('<<ComboboxSelected>>',lambda e,field=label:(self.on_unit_select() if field=='Unit' else self.selection_change()))
            self.cbs[label]=cb
        tk.Label(controls,text='Students',bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=6,sticky='w',padx=5)
        st=ttk.Entry(controls,textvariable=self.students_var,width=8)
        st.grid(row=1,column=6,padx=5,pady=4,sticky='ew')
        self.senior_label=tk.Label(controls,text='',bg='white',fg=MAROON,font=('Segoe UI',9,'bold'))
        self.senior_label.grid(row=2,column=4,columnspan=3,sticky='w',padx=5)

        tk.Label(controls,text='Subject Filter',bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=7,sticky='w',padx=4)
        self.filter_cb=ttk.Combobox(controls,textvariable=self.filter_var,values=['All Subjects'],state='readonly',width=17)
        self.filter_cb.grid(row=1,column=7,padx=4,sticky='ew')
        self.filter_cb.bind('<<ComboboxSelected>>',lambda e:self.draw())
        for c in range(8):controls.grid_columnconfigure(c,weight=1)

        toolbar=tk.Frame(self.root,bg=LIGHT)
        toolbar.pack(fill='x',padx=11,pady=(3,4))
        row1=tk.Frame(toolbar,bg=LIGHT);row1.pack(fill='x')
        if self.role in ('admin','editor'):
            ttk.Button(row1,text='Save',command=self.save).pack(side='left',padx=3,pady=2)
        export_button=ttk.Menubutton(row1,text='Export ▾')
        export_menu=tk.Menu(export_button,tearoff=0)
        for label in EXPORTERS:
            export_menu.add_command(label=label,command=lambda kind=label:self.export(kind))
        export_button.configure(menu=export_menu)
        export_button.pack(side='left',padx=3,pady=2)
        if self.role=='admin':
            ttk.Button(row1,text='Teachers',command=self.teacher_dashboard).pack(side='left',padx=3,pady=2)
            ttk.Button(row1,text='Subjects',command=self.subject_rules).pack(side='left',padx=3,pady=2)
        else:
            ttk.Button(row1,text='Find My Subject',command=self.find_my_subject).pack(side='left',padx=3,pady=2)
        row2=tk.Frame(toolbar,bg=LIGHT);row2.pack(fill='x')
        if self.role=='admin':
          for title,callback in [('Unit Dashboard',self.unit_dashboard),('Add Section',self.add_section),('Delete Section',self.delete_section),
                               ('Check Clashes',self.check_clashes),('Period Timing / Layout',self.period_layout_editor),('Backup Data',self.backup),
                               ('Accounts',self.manage_users),('Change Username / Password',self.change_password),
                               ('Login History',self.login_history),('Logout',self.logout)]:
            ttk.Button(row2,text=title,command=callback).pack(side='left',padx=2,pady=2)
        if self.role!='admin':
            for title,callback in [('Unit Dashboard',self.unit_dashboard),
                                   ('Check Clashes (Read Only)',self.check_clashes),
                                   ('Accounts',self.manage_users),
                                   ('Login History',self.login_history),
                                   ('Backup Data (Admin)',self.backup)]:
                ttk.Button(row2,text=title,command=callback).pack(side='left',padx=2,pady=2)
        row3=tk.Frame(toolbar,bg=LIGHT);row3.pack(fill='x')
        if self.role!='admin':
            ttk.Button(row3,text='Change My Password',command=self.change_password).pack(side='left',padx=2,pady=2)
            ttk.Button(row3,text='Logout',command=self.logout).pack(side='left',padx=2,pady=2)
        ttk.Button(row3,text='Choose Timetable',command=self.back_to_mode_selection).pack(side='left',padx=2,pady=2)

        info_bar=tk.Frame(self.root,bg=LIGHT)
        info_bar.pack(fill='x',padx=14,pady=(6,4))
        self.summary=tk.Label(info_bar,text='Timetables are generated automatically.',bg=LIGHT,fg=MAROON,
                              font=('Segoe UI',12,'bold'),anchor='w')
        self.summary.pack(side='left',fill='x',expand=True)
        tk.Label(info_bar,text='TIMETABLE SIZE',bg=LIGHT,fg=NAVY,
                 font=('Segoe UI',9,'bold')).pack(side='left',padx=(10,5))
        ttk.Button(info_bar,text='A−',width=4,command=lambda:self.change_table_zoom(-.12)).pack(side='left',padx=2)
        ttk.Button(info_bar,text='A+',width=4,command=lambda:self.change_table_zoom(.12)).pack(side='left',padx=2)
        self.zoom_display=tk.Label(info_bar,text=f'{round(self.table_zoom*100)}% • Auto-saved',
                 bg=LIGHT,fg=NAVY,font=('Segoe UI',9,'bold'))
        self.zoom_display.pack(side='left',padx=(8,0))
        area=tk.Frame(self.root,bg=LIGHT)
        area.pack(fill='both',expand=True,padx=12,pady=4)
        self.canvas=tk.Canvas(area,bg='white',highlightthickness=0)
        v=ttk.Scrollbar(area,orient='vertical',command=self.canvas.yview)
        h=ttk.Scrollbar(area,orient='horizontal',command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=h.set,yscrollcommand=v.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');v.grid(row=0,column=1,sticky='ns');h.grid(row=1,column=0,sticky='ew')
        area.grid_columnconfigure(0,weight=1);area.grid_rowconfigure(0,weight=1)
        self.grid_frame=tk.Frame(self.canvas,bg='white')
        self.canvas_window=self.canvas.create_window((0,0),anchor='nw',window=self.grid_frame)
        self.grid_frame.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',self._resize_table_canvas)
        self.subject_count_label=tk.Label(self.root,text='SUBJECT COUNT',bg='#F5F0E4',fg=NAVY,anchor='w',justify='left',wraplength=1500,pady=7)
        self.subject_count_label.pack(fill='x',padx=12)
        self.status=tk.Label(self.root,text='Double-click a period cell to edit. Teacher checks require teacher assignments.',
                             bg='#DDE7F0',fg=NAVY,anchor='w',pady=7)
        self.status.pack(fill='x')
        if self.role=='admin' or not self.find_my_subject(initial=True):
            self.selection_change(force=True)
        # Keep the user's selected zoom instead of resetting to Fit Width.

    def context(self):
        return (self.year_var.get(),self.month_var.get(),self.week_var.get(),self.grade_var.get(),self.section_var.get())

    def guard_unsaved(self):
        if not self.dirty:return True
        response=messagebox.askyesnocancel('Unsaved changes','Save your current timetable before changing selection?')
        if response is None: return False
        if response and not self.save(): return False
        self.dirty=False
        return True

    def on_unit_select(self):
        grades=[g for g in UNIT_GRADES[self.unit_var.get()] if g in self.allowed_grades()]
        if not grades:
            messagebox.showwarning('No access','No classes are assigned to you in this unit.')
            self.unit_var.set(unit_for_grade(self.grade_var.get()))
            return
        self.cbs['Class']['values']=grades
        self.grade_var.set(grades[0])
        sections=self.allowed_sections(grades[0])
        if sections:self.section_var.set(sections[0])
        self.selection_change()

    def selection_change(self,force=False):
        # On a changed selection, save the *previous* class's timetable, never
        # accidentally save old cells under the newly selected class key.
        if not force and self.loaded_context and self.loaded_context != self.context() and self.dirty:
            chosen=self.context()
            variables=(self.year_var,self.month_var,self.week_var,self.grade_var,self.section_var)
            for var,value in zip(variables,self.loaded_context):var.set(value)
            if not self.guard_unsaved(): return
            for var,value in zip(variables,chosen):var.set(value)
        grade=self.grade_var.get()
        if hasattr(self,'unit_var'):
            self.unit_var.set(unit_for_grade(grade))
            self.cbs['Class']['values']=[g for g in UNIT_GRADES[self.unit_var.get()] if g in self.allowed_grades()]
        if grade not in self.allowed_grades():
            messagebox.showwarning('Restricted','This class is not assigned to your account.')
            return
        sections=self.allowed_sections(grade)
        if not sections:return
        self.cbs['Section']['values']=sections
        self.senior_label.configure(text='Section: '+section_display(grade,self.section_var.get()))
        if self.section_var.get() not in sections:self.section_var.set(sections[0])
        self.senior_label.configure(text='Section: '+section_display(grade,self.section_var.get()))
        self.students_var.set(str(self.store.students(grade,self.section_var.get())))
        self.load(silent=True)

    def load(self,silent=False):
        if not silent and not self.guard_unsaved(): return
        ctx=self.context()
        self.slots=(self.store.load(ctx) if self.role=='admin' else
                    self.store.scoped_load(ctx,self.granted_subjects(ctx[3],ctx[4])))
        auto_error=None
        if not self.slots and self.role=='admin':
            # No Generate button is required. A missing timetable is created
            # once on selection; existing saved timetables are never replaced.
            try:
                self.slots=self.scheduler.generate(ctx,variation=secrets.randbits(32))
                self.store.save(ctx,self.slots)
                self.store.log(self.user,'Automatically created timetable '+':'.join(ctx))
            except (TimetableError,ValueError) as error:
                self.slots={}
                auto_error=str(error)
        self.loaded_context=ctx
        self.dirty=False
        self.filter_var.set('All Subjects')
        self.update_filter()
        self.draw()
        if auto_error:
            self.status.config(text='Automatic timetable unavailable: '+auto_error[:220])
            if not silent:messagebox.showwarning('Automatic generation',auto_error)
        elif not silent:
            if self.role!='admin' and not self.slots:
                messagebox.showinfo('Assigned Subject',
                    'Your permission is saved, but this selected week has no matching lesson. '
                    'Use Find My Subject, or ask the administrator to add your subject to the weekly plan.')
            else:
                messagebox.showinfo('Loaded',f'{len(self.slots)} authorized timetable cells loaded from SQLite.')

    def update_filter(self):
        subjects=sorted(set(s.subject for s in self.slots.values()))
        if self.role!='admin':
            subjects=sorted(set(subjects) | self.granted_subjects(self.grade_var.get(),self.section_var.get()))
        self.filter_cb['values']=['All Subjects']+subjects
        if self.filter_var.get() not in self.filter_cb['values']: self.filter_var.set('All Subjects')

    def generate(self):
        if not self.require_admin() or not self.guard_unsaved():return
        try:
            new=self.scheduler.generate(self.context())
            self.store.validate(self.context(),new)
        except (TimetableError,ValueError) as err:
            messagebox.showerror('Cannot generate',str(err));return
        try:
            self.store.save(self.context(), new)
        except (TimetableError, ValueError) as err:
            messagebox.showerror('Automatic save failed',str(err));return
        self.slots=new;self.loaded_context=self.context();self.dirty=False
        self.update_filter();self.draw()
        self.status.config(text='Generated timetable automatically saved in SQLite.')

    def draw(self):
        """Timetable with dedicated break/lunch columns and weekly subject counts."""
        for child in self.grid_frame.winfo_children():child.destroy()
        year,month,week,grade,section=self.context()
        days,times=self.store.layout(grade,section)
        visual=period_columns(times)
        zoom=self.table_zoom
        self._table_period_cells=[]
        day_width=round(125*zoom)
        period_width=round(150*zoom)
        for col,entry in enumerate([None]+visual):
            is_break=entry is not None and entry[3] is None
            self.grid_frame.grid_columnconfigure(col,weight=1,
                minsize=day_width if col==0 else round((100 if is_break else 150)*zoom))
        self.grid_frame.grid_rowconfigure(0,weight=0,minsize=round(75*zoom))
        headers=['DAY']+[f'{label}\n{a} - {b}' for label,a,b,_ in visual]
        for col,heading in enumerate(headers):
            is_break=col>0 and visual[col-1][3] is None
            tk.Label(self.grid_frame,text=heading,font=('Segoe UI',max(9,round(10*zoom)),'bold'),
                bg='#D7AB4E' if is_break else MAROON,fg=MAROON if is_break else 'white',
                width=1,justify='center',wraplength=period_width-16,padx=6,pady=10,
                bd=1,relief='solid').grid(row=0,column=col,sticky='nsew')
        for row,day in enumerate(days,1):
            self.grid_frame.grid_rowconfigure(row,weight=1,minsize=round(96*zoom))
            tk.Label(self.grid_frame,text=day,font=('Segoe UI',max(10,round(11*zoom)),'bold'),
                bg='#F5EACE',fg=MAROON,width=1,padx=8,pady=14,
                bd=1,relief='solid').grid(row=row,column=0,sticky='nsew')
            for col,(label,a,b,p) in enumerate(visual,1):
                if p is None:
                    tk.Label(self.grid_frame,text=label.upper(),font=('Segoe UI',max(9,round(10*zoom)),'bold'),
                        bg='#FFF2C9',fg=MAROON,width=1,wraplength=95,justify='center',
                        bd=1,relief='solid').grid(row=row,column=col,sticky='nsew')
                    continue
                slot=self.slots.get((day,p))
                subject=slot.subject if slot else ('---' if self.role=='admin' else '—')
                teacher=(slot.teacher or self.store.teacher_for(grade,section,subject)) if slot else ''
                show=subject+(f'\n{teacher}' if teacher else '')
                dim=self.filter_var.get() not in ('All Subjects',subject)
                bg='#F0F2F5' if dim else (slot.bg if slot else 'white')
                fg='#ADB4BE' if dim else (slot.fg if slot else TEXT)
                lab=tk.Label(self.grid_frame,text=show,width=1,wraplength=period_width-20,
                    justify='center',font=('Segoe UI',max(9,round(10*zoom)),'bold'),
                    bg=bg,fg=fg,padx=7,pady=12,bd=1,relief='solid',
                    cursor='hand2' if self.can_edit() and slot else 'arrow')
                lab.grid(row=row,column=col,sticky='nsew')
                lab.bind('<Double-Button-1>',lambda event,d=day,i=p:self.edit_cell(d,i))
                self._table_period_cells.append(lab)
        name=f'CLASS {grade}-{section_display(grade,section)} | {week} | {month} {year} | Students: {self.students_var.get()}'
        name+=f" | {'UNSAVED CHANGES' if self.dirty else 'SAVED' if self.slots else 'NOT GENERATED'}"
        self.summary.config(text=name)
        counts=subject_counts(self.slots)
        count_text='   •   '.join(f'{subject}: {amount}' for subject,amount in sorted(counts.items()))
        self.subject_count_label.config(text='SUBJECT COUNT (WEEK)   '+(count_text or 'No periods assigned'))
        self._fit_grid_to_canvas()
        breaks=[f'{label} {a}–{b}' for label,a,b,p in visual if p is None]
        self.status.config(text=f'{len(times)} periods | School ends {times[-1][1]}  •  '+ '  |  '.join(breaks))

    def edit_cell(self,day,p):
        if not self.can_edit():
            messagebox.showwarning('Restricted','Viewer access is read-only.');return
        if not self.slots:return
        grade=self.grade_var.get();section=self.section_var.get()
        if self.role!='admin' and (day,p) not in self.slots:
            messagebox.showwarning('Restricted','You cannot open another subject block.')
            return
        old=self.slots.get((day,p),Slot(day,p,'---'))
        win=tk.Toplevel(self.root);win.title(f'Edit: {day}, period {p+1}');win.geometry('430x360');win.transient(self.root);win.grab_set()
        panel=ttk.Frame(win,padding=20);panel.pack(fill='both',expand=True)
        subject=text_entry(panel,'Subject or activity',default=old.subject)
        if self.role!='admin':
            subject.configure(state='readonly')
        teacher=text_entry(panel,'Teacher (optional override)',default=old.teacher)
        chosen=[old.bg,old.fg]
        def choose_color(which):
            value=colorchooser.askcolor(color=chosen[which],parent=win)[1]
            if value:chosen[which]=value
        ttk.Button(panel,text='Choose background colour',command=lambda:choose_color(0)).pack(pady=(11,3),anchor='w')
        ttk.Button(panel,text='Choose text colour',command=lambda:choose_color(1)).pack(pady=3,anchor='w')
        def apply():
            s=subject.get().strip()
            if not s:
                messagebox.showwarning('Missing subject','Enter subject or --- for empty.',parent=win);return
            if self.role!='admin' and s.strip().casefold() not in {
                    value.strip().casefold() for value in self.granted_subjects(grade,section)}:
                messagebox.showerror('Restricted','You may edit only your assigned subject.',parent=win);return
            updated=replace(old,subject=s,teacher=teacher.get().strip(),bg=chosen[0],fg=chosen[1])
            copy=dict(self.slots);copy[(day,p)]=updated
            try:
                if self.role=='admin':self.store.validate(self.context(),copy)
                else:self.store.validate(self.context(),{**self.store.load(self.context()),**copy})
            except TimetableError as err:
                messagebox.showerror('Teacher Clash',str(err),parent=win);return
            try:
                if self.role == 'admin':
                    self.store.save(self.context(), copy)
                else:
                    self.store.save_subject_edits(self.context(), {(day,p):updated},
                                                  self.granted_subjects(grade,section))
            except (TimetableError, ValueError) as error:
                messagebox.showerror('Auto-save failed', str(error), parent=win)
                return
            self.slots=copy;self.dirty=False;self.loaded_context=self.context()
            self.store.log(self.user, 'Auto-saved subject edit '+':'.join(self.context()))
            self.update_filter();self.draw();win.destroy()
        ttk.Button(panel,text='Apply edit',command=apply).pack(pady=8,fill='x')

    def save(self):
        if not self.require_edit():return False
        if not self.slots:
            messagebox.showwarning('Nothing to save','No timetable available. Check the Unit Dashboard and teacher assignments.');return False
        try:
            if self.role=='admin':
                self.store.save(self.context(),self.slots)
                self.store.set_students(self.grade_var.get(),self.section_var.get(),self.students_var.get())
            else:
                allowed=self.granted_subjects(self.grade_var.get(),self.section_var.get())
                # Only write modified authorized subject cells; no other lessons can change.
                original=self.store.scoped_load(self.context(),allowed)
                changed={key:slot for key,slot in self.slots.items() if original.get(key)!=slot}
                if changed:self.store.save_subject_edits(self.context(),changed,allowed)
            self.store.log(self.user,'Saved timetable '+':'.join(self.context()))
        except (TimetableError,ValueError,Exception) as err:
            messagebox.showerror('Save failed',str(err));return False
        self.dirty=False;self.loaded_context=self.context();self.draw()
        messagebox.showinfo('Saved','The timetable was saved permanently to the SQLite database.')
        return True

    def save_students(self):
        if not self.require_admin():return
        try:self.store.set_students(self.grade_var.get(),self.section_var.get(),self.students_var.get())
        except TimetableError as err:messagebox.showerror('Students',str(err));return
        self.draw();messagebox.showinfo('Saved','Student count saved.')

    def export(self,kind):
        if not self.slots:
            messagebox.showwarning('No timetable','Select a class with an available timetable first.');return
        if self.dirty:
            response=messagebox.askyesnocancel('Unsaved edits','Save the timetable before exporting?')
            if response is None:return
            if response and not self.save():return
        ext,writer=EXPORTERS[kind]
        year,month,week,grade,section=self.context()
        suggested=f'APMHSS_{grade}_{section}_{month}_{week.replace(" ","")}{ext}'
        path=filedialog.asksaveasfilename(defaultextension=ext,initialfile=suggested,
                                          filetypes=[(kind,'*'+ext)])
        if not path:return
        try:writer(path,self.context(),self.slots,self.store)
        except ImportError as err:
            messagebox.showerror('Missing export package',f'{err}\n\nRun:\npython -m pip install pillow python-docx reportlab openpyxl');return
        except Exception as err:messagebox.showerror('Export failed',str(err));return
        messagebox.showinfo('Export complete','File saved to:\n'+path)

    def add_section(self):
        if not self.require_admin() or not self.guard_unsaved():return
        value=simpledialog.askstring('Add Section','Enter section name (e.g. D, B3):',parent=self.root)
        if value is None:return
        try:self.store.add_section(self.grade_var.get(),value)
        except Exception as err:messagebox.showerror('Cannot add',str(err));return
        self.selection_change(force=True)
        self.cbs['Section']['values']=self.store.sections(self.grade_var.get())
        self.section_var.set(value.strip().upper());self.selection_change(force=True)

    def rename_section(self):
        if not self.require_admin() or not self.guard_unsaved():
            return
        grade=self.grade_var.get()
        old=self.section_var.get()
        value=simpledialog.askstring('Rename Section',
            f'Enter a new name for Class {grade}-{old}:',
            initialvalue=old,parent=self.root)
        if value is None:
            return
        try:
            new=self.store.rename_section(grade,old,value)
        except Exception as err:
            messagebox.showerror('Rename failed',str(err));return
        if self.mode=='class':
            self.edification.rename_section(grade,old,new)
        self.section_var.set(new)
        self.cbs['Section']['values']=self.store.sections(grade)
        self.selection_change(force=True)
        self.store.log(self.user,f'Renamed Class {grade}-{old} to {grade}-{new}')
        messagebox.showinfo('Section renamed',f'Class {grade}-{old} is now {grade}-{new}.')

    def delete_section(self):
        if not self.require_admin() or not self.guard_unsaved():return
        grade=self.grade_var.get();sec=self.section_var.get()
        if not messagebox.askyesno('Delete Section',f'Delete {grade}-{sec} and its saved timetables and teacher assignments?'):return
        if not self.guard_unsaved():return
        try:
            self.store.delete_section(grade,sec)
            if self.mode=='class':self.edification.delete_section(grade,sec)
        except Exception as err:messagebox.showerror('Cannot delete',str(err));return
        self.selection_change(force=True)

    def subject_rules(self):
        if not self.require_admin():return
        grade=self.grade_var.get();section=self.section_var.get()
        mains,extras=self.store.subjects(grade,section)
        win=tk.Toplevel(self.root);win.title('Configure Subjects');win.geometry('540x405');win.transient(self.root);win.grab_set()
        panel=ttk.Frame(win,padding=20);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=f'Subject rules for Class {grade}-{section}',font=('Segoe UI',13,'bold')).pack(anchor='w')
        a=text_entry(panel,'Main subjects (comma separated)',default=', '.join(mains))
        b=text_entry(panel,'Extra activities and frequency (comma separated)',default=', '.join(extras))
        ttk.Label(panel,text='Repeat an extra subject to schedule it more than once.\nPeriod counts and timings follow the original school timetable.').pack(pady=13,anchor='w')
        def save_rules():
            ml=[x.strip() for x in a.get().split(',') if x.strip()]
            el=[x.strip() for x in b.get().split(',') if x.strip()]
            try:self.store.set_subjects(grade,section,ml,el)
            except TimetableError as err:messagebox.showerror('Invalid subjects',str(err),parent=win);return
            win.destroy();messagebox.showinfo('Updated','New subjects will be used when you regenerate the timetable.')
        ttk.Button(panel,text='Save Subject Rules',command=save_rules).pack(pady=8,fill='x')
        ttk.Button(panel,text='Teacher Subject Access / Accounts',command=self.manage_users).pack(pady=5,fill='x')

    def unit_dashboard(self):
        """Manage weekly subject quotas for every grade and section in five units."""
        if self.role!='admin':
            return self.view_assigned_unit_dashboard()
        win=tk.Toplevel(self.root)
        win.title('UNIT DASHBOARD | Subject and Weekly Period Allocation')
        win.geometry('1000x680')
        win.minsize(840,590)
        win.transient(self.root)
        win.grab_set()
        outer=ttk.Frame(win,padding=17);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='UNIT DASHBOARD',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(outer,text='Select Unit → Class → Section. Add or remove subjects and set exact periods per week.',
                  font=('Segoe UI',10)).pack(anchor='w',pady=(0,12))
        choices=ttk.Frame(outer);choices.pack(fill='x',pady=(0,10))
        unit=tk.StringVar(value=unit_for_grade(self.grade_var.get()))
        grade=tk.StringVar(value=self.grade_var.get())
        section=tk.StringVar(value=self.section_var.get())
        selectors={}
        for name,var,values in [('Unit',unit,list(UNIT_GRADES)),
                                ('Class',grade,list(UNIT_GRADES[unit.get()])),
                                ('Section',section,self.store.sections(grade.get()))]:
            col=ttk.Frame(choices);col.pack(side='left',padx=(0,18))
            ttk.Label(col,text=name).pack(anchor='w')
            cb=ttk.Combobox(col,textvariable=var,values=values,state='readonly',width=18)
            cb.pack(pady=3)
            selectors[name]=cb
        ttk.Label(choices,text='Changes are saved separately for each class and section.',
                  foreground='#52647C').pack(side='left',padx=15)

        table_frame=ttk.Frame(outer);table_frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(table_frame,columns=('Subject','Periods'),show='headings',selectmode='browse',height=9)
        tree.heading('Subject',text='SUBJECT / ACTIVITY')
        tree.heading('Periods',text='PERIODS PER WEEK')
        tree.column('Subject',width=470,anchor='w')
        tree.column('Periods',width=210,anchor='center')
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left',fill='both',expand=True)
        scroll.pack(side='left',fill='y')
        summary=tk.StringVar()
        ttk.Label(outer,textvariable=summary,font=('Segoe UI',11,'bold'),
                  foreground=MAROON).pack(anchor='w',pady=(9,4))
        form=ttk.Frame(outer);form.pack(fill='x',pady=(4,8))
        name_var=tk.StringVar()
        count_var=tk.StringVar(value='5')
        ttk.Label(form,text='Subject name').grid(row=0,column=0,sticky='w')
        ttk.Label(form,text='Periods / week').grid(row=0,column=1,sticky='w',padx=10)
        ttk.Entry(form,textvariable=name_var,width=36).grid(row=1,column=0,sticky='ew')
        ttk.Spinbox(form,from_=1,to=48,textvariable=count_var,width=12).grid(row=1,column=1,sticky='w',padx=10)
        draft={}
        state={'changed':False,'original':''}
        def refresh_table():
            tree.delete(*tree.get_children())
            for subject,number in sorted(draft.items(),key=lambda item:item[0].casefold()):
                tree.insert('',tk.END,values=(subject,number))
            total=sum(draft.values());capacity=self.store.capacity(grade.get(),section.get())
            remainder=capacity-total
            state_label='Saved custom plan' if self.store.has_weekly_plan(grade.get(),section.get()) else 'Suggested defaults (not saved)'
            summary.set(f'{unit.get()}  •  Class {grade.get()}-{section.get()}  |  Allocated: {total}/{capacity}  |  Unallocated: {remainder}  |  {state_label}' +
                        ('  •  UNSAVED EDITS' if state['changed'] else ''))
        def load_plan():
            draft.clear();draft.update(self.store.weekly_plan(grade.get(),section.get()))
            state['changed']=False;state['original']=''
            name_var.set('');count_var.set('5')
            refresh_table()
        def discard_changes():
            return not state['changed'] or messagebox.askyesno('Unsaved subject changes',
                'Discard the subject/period edits for this class?',parent=win)
        def on_unit(*_):
            if not discard_changes():
                unit.set(unit_for_grade(grade.get()))
                return
            grades=UNIT_GRADES[unit.get()]
            grade.set(grades[0]);selectors['Class']['values']=grades
            sections=self.store.sections(grade.get())
            selectors['Section']['values']=sections;section.set(sections[0]);load_plan()
        def on_grade(*_):
            if not discard_changes():
                grade.set(state.get('last_grade',grade.get()))
                return
            unit.set(unit_for_grade(grade.get()))
            selectors['Class']['values']=UNIT_GRADES[unit.get()]
            sections=self.store.sections(grade.get())
            selectors['Section']['values']=sections
            section.set(sections[0]);load_plan()
            state['last_grade']=grade.get()
        def on_section(*_):
            if not discard_changes():
                section.set(state.get('last_section',section.get()))
                return
            load_plan();state['last_section']=section.get()
        selectors['Unit'].bind('<<ComboboxSelected>>',on_unit)
        selectors['Class'].bind('<<ComboboxSelected>>',on_grade)
        selectors['Section'].bind('<<ComboboxSelected>>',on_section)
        def on_selected(*_):
            selected=tree.selection()
            if not selected:return
            name,periods=tree.item(selected[0],'values')
            name_var.set(name);count_var.set(str(periods))
            state['original']=name
        tree.bind('<<TreeviewSelect>>',on_selected)
        def add_or_update():
            name=name_var.get().strip()
            if not name or name=='---' or len(name)>55:
                messagebox.showwarning('Subject','Enter a subject name (up to 55 characters).',parent=win);return
            try:
                number=int(count_var.get())
                if str(number)!=count_var.get().strip() or number<1:
                    raise ValueError
            except ValueError:
                messagebox.showwarning('Periods','Enter a positive whole number.',parent=win);return
            original=state['original']
            updated=dict(draft)
            if original and original in updated and original!=name:del updated[original]
            if any(existing.casefold()==name.casefold() and existing!=name for existing in updated):
                messagebox.showwarning('Duplicate','This subject already exists.',parent=win);return
            updated[name]=number
            capacity=self.store.capacity(grade.get(),section.get())
            if sum(updated.values())>capacity:
                messagebox.showwarning('Too many periods',
                    f'Only {capacity} weekly periods are available for Class {grade.get()}.\n'
                    f'Your total is {sum(updated.values())}. Reduce the number of periods.',parent=win)
                return
            draft.clear();draft.update(updated)
            state['changed']=True
            if save_plan():
                state['original']=''
                name_var.set('');count_var.set('5')
            refresh_table()
        def remove_subject():
            selected=tree.selection()
            if not selected:
                messagebox.showinfo('Select subject','Select a subject in the table first.',parent=win);return
            name=tree.item(selected[0],'values')[0]
            if not messagebox.askyesno('Remove subject',f'Remove {name} from Class {grade.get()}-{section.get()}?',parent=win):return
            if len(draft)==1:
                messagebox.showwarning('Last subject','Add a replacement subject before removing the last one.',parent=win)
                return
            draft.pop(name,None)
            state['changed']=True
            if save_plan():
                state['original']=''
                name_var.set('')
            refresh_table()
        def save_plan(apply_to_all=False):
            # Do not silently overwrite unsaved manual timetable edits.
            if self.dirty and not self.guard_unsaved():
                load_plan()
                return False
            if not draft:
                messagebox.showwarning('No subjects','Add at least one subject.',parent=win)
                return False
            targets=self.store.sections(grade.get()) if apply_to_all else [section.get()]
            if apply_to_all and len(targets)>1:
                if not messagebox.askyesno('Apply to all sections',
                    f'Automatically update subject plans and saved timetables for ALL {len(targets)} sections of Class {grade.get()}?\n'
                    "This replaces the affected sections' existing saved timetables.",parent=win):
                    load_plan()
                    return False
            try:
                count=self.store.apply_plan_and_regenerate(
                    grade.get(),targets,dict(draft),self.context(),
                    variation=secrets.randbits(32))
                self.store.log(self.user,'Auto updated subjects and timetables for '+
                               grade.get()+':'+','.join(targets))
            except (TimetableError,ValueError,Exception) as err:
                messagebox.showerror('Automatic update failed',
                    'Your previous subjects and saved timetables were kept.\n\n'+str(err),parent=win)
                load_plan()
                return False
            state['changed']=False
            refresh_table()
            if self.grade_var.get()==grade.get() and self.section_var.get() in targets:
                self.load(silent=True)
            self.status.config(text=f'Auto-saved subject plan and regenerated {count} timetable(s) without teacher clashes.')
            return True
        ttk.Button(form,text='Add / Update',command=add_or_update).grid(row=1,column=2,padx=6)
        ttk.Button(form,text='Remove Selected',command=remove_subject).grid(row=1,column=3,padx=4)
        all_sections=tk.BooleanVar(value=False)
        ttk.Checkbutton(outer,text='Change this for all sections of the selected class',
                        variable=all_sections).pack(anchor='w',pady=(5,4))
        # Visible only while the all-sections checkbox is selected.
        update_all=ttk.Button(outer,text='UPDATE ALL SECTIONS',
                              command=lambda:save_plan(apply_to_all=True))
        def toggle_update_button(*_):
            if all_sections.get():
                update_all.pack(anchor='w',pady=(0,7))
            else:
                update_all.pack_forget()
        all_sections.trace_add('write',toggle_update_button)
        bottom=ttk.Frame(outer);bottom.pack(fill='x',pady=(4,0))
        ttk.Label(bottom,text='AUTO-SAVE ON: Add / Update / Remove automatically refreshes timetables.',
                  foreground='#187245').pack(side='left',padx=(0,8))
        ttk.Button(bottom,text='Close',command=lambda:close_window()).pack(side='left')
        ttk.Button(bottom,text='Teacher Access',command=self.manage_users).pack(side='left',padx=6)
        ttk.Label(bottom,text='Example: Tamil = 5 periods per week',foreground='#53657B').pack(side='right')
        def close_window():
            if discard_changes():win.destroy()
        win.protocol('WM_DELETE_WINDOW',close_window)
        state['last_grade']=grade.get();state['last_section']=section.get()
        load_plan()

    def view_assigned_unit_dashboard(self):
        """Read-only dashboard showing ONLY authorized subject plans."""
        win=tk.Toplevel(self.root);win.title('UNIT DASHBOARD • My Assigned Subjects')
        win.geometry('750x520');win.transient(self.root)
        pane=ttk.Frame(win,padding=20);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text='UNIT DASHBOARD • MY SUBJECTS',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(pane,text='Read-only access. Other teachers’ subjects and sections are hidden.',
                  foreground='#52647C').pack(anchor='w',pady=6)
        tree=ttk.Treeview(pane,columns=('Unit','Class','Section','Subject','Weekly Periods'),
                          show='headings',height=14)
        for col,w in [('Unit',110),('Class',65),('Section',95),('Subject',230),('Weekly Periods',120)]:
            tree.heading(col,text=col);tree.column(col,width=w)
        tree.pack(fill='both',expand=True,pady=12)
        for grade,section,subject in self.auth_store.grants(self.user):
            if section not in self.store.sections(grade):continue
            plan=self.store.weekly_plan(grade,section)
            periods=next((n for name,n in plan.items() if name.strip().casefold()==subject.strip().casefold()),'—')
            tree.insert('',tk.END,values=(unit_for_grade(grade),grade,
                        section_display(grade,section),subject,periods))
        ttk.Button(pane,text='Close',command=win.destroy).pack(anchor='e')

    def teacher_directory(self):
        """Administrator-only school staff directory and descriptions."""
        if not self.require_admin():return
        win=tk.Toplevel(self.root);win.title('Teacher Directory • APMHSS')
        win.geometry('790x590');win.transient(self.root)
        pane=ttk.Frame(win,padding=16);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text='TEACHER DIRECTORY',font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(pane,text='Teacher names and descriptions (not login credentials).',
                  foreground='#52647C').pack(anchor='w',pady=(2,12))
        tree=ttk.Treeview(pane,columns=('Name','Description'),show='headings',height=11)
        tree.heading('Name',text='TEACHER NAME');tree.heading('Description',text='DESCRIPTION / SUBJECT')
        tree.column('Name',width=230);tree.column('Description',width=490)
        tree.pack(fill='both',expand=True)
        profile_ids={}
        def refresh():
            tree.delete(*tree.get_children());profile_ids.clear()
            for row in self.auth_store.teacher_profiles():
                item=tree.insert('',tk.END,values=(row['name'],row['description']))
                profile_ids[item]=row['id']
        refresh()
        if self.role=='admin':
            form=ttk.LabelFrame(pane,text='Add / Edit Teacher',padding=10);form.pack(fill='x',pady=12)
            name=tk.StringVar();description=tk.StringVar();selected=[None]
            ttk.Label(form,text='Name').grid(row=0,column=0,sticky='w')
            ttk.Label(form,text='Description / Subject').grid(row=0,column=1,sticky='w')
            ttk.Entry(form,textvariable=name,width=27).grid(row=1,column=0,sticky='ew',padx=(0,10))
            ttk.Entry(form,textvariable=description,width=55).grid(row=1,column=1,sticky='ew')
            form.grid_columnconfigure(1,weight=1)
            def pick(*_):
                if not tree.selection():return
                item=tree.selection()[0];selected[0]=profile_ids[item]
                n,d=tree.item(item,'values');name.set(n);description.set(d)
            tree.bind('<<TreeviewSelect>>',pick)
            def save_profile():
                try:
                    if selected[0] is None:self.auth_store.add_teacher_profile(name.get(),description.get())
                    else:self.auth_store.update_teacher_profile(selected[0],name.get(),description.get())
                except (TimetableError,ValueError) as error:
                    messagebox.showerror('Teacher',str(error),parent=win);return
                selected[0]=None;name.set('');description.set('');refresh()
            def delete_profile():
                if selected[0] is None:return
                if not messagebox.askyesno('Delete Teacher','Delete this directory entry?',parent=win):return
                self.auth_store.delete_teacher_profile(selected[0]);selected[0]=None
                name.set('');description.set('');refresh()
            buttons=ttk.Frame(form);buttons.grid(row=2,column=0,columnspan=2,sticky='w',pady=(10,0))
            ttk.Button(buttons,text='Save Teacher',command=save_profile).pack(side='left',padx=4)
            ttk.Button(buttons,text='Delete Selected',command=delete_profile).pack(side='left',padx=4)
            ttk.Button(buttons,text='Clear',command=lambda:(selected.__setitem__(0,None),name.set(''),description.set(''))).pack(side='left',padx=4)
        ttk.Button(pane,text='Close',command=win.destroy).pack(anchor='e',pady=5)

    def period_layout_editor(self):
        """Auto-generate periods from start/end and two short breaks + lunch."""
        if not self.require_admin() or not self.guard_unsaved():return
        grade,section=self.grade_var.get(),self.section_var.get()
        days,times=self.store.layout(grade,section)
        detected=[(a,b) for label,a,b,p in period_columns(times) if p is None]
        # Preserve current break times when available; otherwise suggest editable values.
        default_breaks=[('10:25','10:35'),('12:35','13:05'),('14:25','14:35')]
        if len(detected)==3: default_breaks=detected
        win=tk.Toplevel(self.root);win.title(f'Automatic Period Timing | {grade}-{section}')
        win.geometry('610x610');win.transient(self.root);win.grab_set()
        frame=ttk.Frame(win,padding=20);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='AUTOMATIC PERIOD TIMING',font=('Segoe UI',17,'bold')).pack(anchor='w',pady=(0,12))
        ttk.Label(frame,text='Enter start/end time, two short breaks, lunch and number of periods.\n'
            'The application will calculate each period automatically (24-hour HH:MM).',
            wraplength=540).pack(anchor='w',pady=(0,10))
        inputs=ttk.Frame(frame);inputs.pack(fill='x')
        entries={}
        defaults={'School Start':times[0][0],'School End':times[-1][1],
            'Short Break 1 Start':default_breaks[0][0],'Short Break 1 End':default_breaks[0][1],
            'Lunch Start':default_breaks[1][0],'Lunch End':default_breaks[1][1],
            'Short Break 2 Start':default_breaks[2][0],'Short Break 2 End':default_breaks[2][1],
            'Number of Periods':str(len(times)),'School Days':str(len(days))}
        for i,(name,value) in enumerate(defaults.items()):
            ttk.Label(inputs,text=name).grid(row=i,column=0,sticky='w',pady=5,padx=4)
            var=tk.StringVar(value=value);entries[name]=var
            ttk.Entry(inputs,textvariable=var,width=17).grid(row=i,column=1,sticky='w',pady=5,padx=4)
        preview=tk.Text(frame,height=7,width=68,wrap='word',state='disabled')
        preview.pack(fill='both',expand=True,pady=10)
        proposed=[None]
        def calculate():
            try:
                times_new=make_periods(entries['School Start'].get(),entries['School End'].get(),
                    (entries['Short Break 1 Start'].get(),entries['Short Break 1 End'].get()),
                    (entries['Lunch Start'].get(),entries['Lunch End'].get()),
                    (entries['Short Break 2 Start'].get(),entries['Short Break 2 End'].get()),
                    entries['Number of Periods'].get())
                count=int(entries['School Days'].get())
                self.store.validate_layout(count,times_new)
                proposed[0]=(count,times_new)
                text='\n'.join(f'{label}: {a} – {b}' for label,a,b,_ in period_columns(times_new))
                preview.configure(state='normal');preview.delete('1.0','end');preview.insert('end',text)
                preview.configure(state='disabled')
                return True
            except (ValueError,TimetableError) as err:
                proposed[0]=None
                messagebox.showerror('Invalid timings',str(err),parent=win)
                return False
        def commit():
            if not calculate():return
            day_count,new_times=proposed[0]
            if not messagebox.askyesno('Apply layout',
                'Save these timings and regenerate saved timetables for this class/section?\n'
                'Previous data will be preserved if generation fails.',parent=win):return
            try:
                self.store.apply_layout_and_regenerate(grade,section,day_count,new_times,self.context())
                self.store.log(self.user,f'Automatic timing layout {grade}-{section}')
            except Exception as err:
                messagebox.showerror('Layout not changed',str(err),parent=win);return
            win.destroy();self.load(silent=True)
            messagebox.showinfo('Saved','Period timings and timetable saved in SQLite.')
        actions=ttk.Frame(frame);actions.pack(fill='x')
        ttk.Button(actions,text='Preview Automatic Periods',command=calculate).pack(side='left',padx=3)
        ttk.Button(actions,text='Save Timing & Generate',command=commit).pack(side='left',padx=3)
        ttk.Button(actions,text='Cancel',command=win.destroy).pack(side='left',padx=3)

    def teacher_dashboard(self):
        if not self.require_admin():return
        win=tk.Toplevel(self.root);win.title('Teacher and Subject Dashboard');win.geometry('1150x630');win.transient(self.root)
        frame=ttk.Frame(win,padding=15);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Teacher Assignments (saved in SQLite)',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=5)
        line=ttk.Frame(frame);line.pack(fill='x',pady=8)
        g=tk.StringVar(value=self.grade_var.get());sec=tk.StringVar(value=self.section_var.get())
        sub=tk.StringVar();teacher=tk.StringVar()
        for label,variable,choices,w in [('Class',g,list(GRADES),9),('Section',sec,self.store.sections(g.get()),9),('Subject',sub,[],24)]:
            group_frame=ttk.Frame(line);group_frame.pack(side='left',padx=4)
            ttk.Label(group_frame,text=label).pack(anchor='w')
            cb=ttk.Combobox(group_frame,textvariable=variable,values=choices,width=w)
            cb.pack()
            if label=='Section':section_cb=cb
            elif label=='Subject':subject_cb=cb
        tframe=ttk.Frame(line);tframe.pack(side='left',padx=4)
        ttk.Label(tframe,text='Teacher name').pack(anchor='w');ttk.Entry(tframe,textvariable=teacher,width=22).pack()
        reference_bar=ttk.Frame(frame);reference_bar.pack(fill='x',pady=(2,6))
        ttk.Label(reference_bar,text='Teacher names from supplied 2026–27 PDF (review before assigning):').pack(side='left',padx=4)
        pdf_choice=tk.StringVar()
        pdf_combo=ttk.Combobox(reference_bar,textvariable=pdf_choice,width=53,state='readonly')
        pdf_combo.pack(side='left',padx=4)
        pdf_entries=[]
        def refresh_pdf_choices():
            pdf_entries[:]=self.auth_store.pdf_teacher_choices(g.get(),sec.get())
            pdf_combo['values']=[f"{r['subject']} — {r['teacher']}" for r in pdf_entries]
            pdf_choice.set('')
        def select_pdf_teacher(*_):
            ix=pdf_combo.current()
            if ix<0:return
            entry=pdf_entries[ix]
            teacher.set(entry['teacher'])
            key=entry['subject'].upper().replace('.','').strip()
            aliases={'COMPUTER SCIENCE':'Comp. Sci','COMP SCIENCE':'Comp. Sci',
                'SOCIAL STUDIES':'Social Sci','SOCIAL SCIENCE':'Social Sci',
                'PHYSICAL EDUCATION':'PET','SAKTHINERI':'Sakthi Neri',
                'BUSINESS MATHS':'Bus. Maths','ACCOUNTANCY':'Accounts',
                'COMPUTER APPLICATION':'Comp. App','BIO BOTANY':'Maths / Biology',
                'BIO ZOOLOGY':'Maths / Biology','BIOLOGY':'Maths / Biology'}
            desired=aliases.get(key,key.title())
            if g.get() in ('11','12') and sec.get()=='B' and key in ('MATHS','BIO BOTANY','BIO ZOOLOGY','BIOLOGY'):
                desired='Maths / Biology'
            available=list(subject_cb['values'])
            found=next((v for v in available if v.casefold()==desired.casefold()),None)
            if found:sub.set(found)
        pdf_combo.bind('<<ComboboxSelected>>',select_pdf_teacher)

        columns=['Class','Section','Subject','Teacher']
        table=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse')
        for col in columns:table.heading(col,text=col);table.column(col,width=110 if col!='Teacher' else 210)
        table.pack(fill='both',expand=True,pady=12)
        def refresh_choices(*args):
            secs=self.store.sections(g.get());section_cb['values']=secs
            if sec.get() not in secs: sec.set(secs[0])
            main,extra=self.store.subjects(g.get(),sec.get())
            subject_cb['values']=sorted(set(main+extra+(['SKKA'] if g.get() in ('6','7','8') else [])))
            refresh_pdf_choices()
        def refresh():
            table.delete(*table.get_children())
            for row in self.store.assignments():
                table.insert('',tk.END,values=(row['grade'],row['section'],row['subject'],row['teacher']))
            self.draw()
        g.trace_add('write',refresh_choices);sec.trace_add('write',refresh_choices)
        refresh_choices()
        bottom=ttk.Frame(frame);bottom.pack(fill='x')
        def assign_teacher():
            previous=self.store.teacher_for(g.get(),sec.get(),sub.get())
            try:
                self.store.assign(g.get(),sec.get(),sub.get(),teacher.get())
                collisions=self.all_saved_conflicts()
                if collisions:
                    if previous:self.store.assign(g.get(),sec.get(),sub.get(),previous)
                    else:self.store.unassign(g.get(),sec.get(),sub.get())
                    raise TimetableError('This assignment creates clashes in saved timetables:\n'+'\n'.join(collisions[:7]))
            except (TimetableError,ValueError) as err:messagebox.showerror('Assignment rejected',str(err),parent=win);return
            refresh()
        def remove_teacher():
            sel=table.selection()
            if not sel:return
            a,b,c,_=table.item(sel[0],'values')
            if messagebox.askyesno('Remove teacher',f'Remove assignment {a}-{b} / {c}?',parent=win):
                self.store.unassign(a,b,c);refresh()
        def apply_pdf_unique():
            """Approve only PDF teacher/subject pairs with a single candidate."""
            from collections import defaultdict
            available=list(subject_cb['values'])
            alias={'COMPUTER SCIENCE':'Comp. Sci','COMP SCIENCE':'Comp. Sci',
                   'SOCIAL STUDIES':'Social Sci','SOCIAL SCIENCE':'Social Sci',
                   'PHYSICAL EDUCATION':'PET','SAKTHINERI':'Sakthi Neri',
                   'BUSINESS MATHS':'Bus. Maths','ACCOUNTANCY':'Accounts',
                   'COMPUTER APPLICATION':'Comp. App'}
            groups=defaultdict(dict)
            for row in self.auth_store.pdf_teacher_choices(g.get(),sec.get()):
                key=row['subject'].upper().strip()
                if g.get() in ('11','12') and sec.get()=='B' and key in ('MATHS','BIOLOGY','BIO BOTANY','BIO ZOOLOGY'):
                    key='Maths / Biology'
                else:key=alias.get(key,key.title())
                target=next((x for x in available if x.casefold()==key.casefold()),None)
                if target:
                    groups[target][row['teacher'].casefold()]=row['teacher']
            added=0;ambiguous=[];rejected=[]
            for subject,names in groups.items():
                if self.store.teacher_for(g.get(),sec.get(),subject):continue
                if len(names)!=1:
                    ambiguous.append(subject);continue
                teacher_name=next(iter(names.values()))
                self.store.assign(g.get(),sec.get(),subject,teacher_name)
                conflicts=self.all_saved_conflicts()
                if conflicts:
                    self.store.unassign(g.get(),sec.get(),subject)
                    rejected.append(subject)
                else:added+=1
            refresh()
            messagebox.showinfo('PDF teacher import',
                f'Saved {added} reviewed-by-rule unique teacher assignment(s) to SQLite.\n'
                f'Needs manual review (multiple teachers): {len(ambiguous)}\n'
                f'Skipped due to clashes: {len(rejected)}.\n\n'
                'You can choose remaining names from the PDF dropdown and save them.',parent=win)
        ttk.Button(bottom,text='Import Unique PDF Teachers',command=apply_pdf_unique).pack(side='left',padx=4)
        ttk.Button(bottom,text='Add / Update Assignment',command=assign_teacher).pack(side='left',padx=5)
        ttk.Button(bottom,text='Remove Selected',command=remove_teacher).pack(side='left',padx=5)
        ttk.Button(bottom,text='Refresh',command=refresh).pack(side='left',padx=5)
        ttk.Label(bottom,text='Enter one teacher for each class, section, subject.').pack(side='right')
        refresh()

    def all_saved_conflicts(self):
        year,month,week,*_=self.context()
        scopes=[(r['grade'],r['section']) for r in self.store.db.execute('''SELECT DISTINCT grade,section FROM schedules
                                                 WHERE year=? AND month=? AND week=?''',(year,month,week))]
        problems=[]
        for grade,section in scopes:
            ctx=year,month,week,grade,section
            problems.extend(self.store.conflicts(ctx,self.store.load(ctx)))
        return sorted(set(problems))

    def assigned_clash_report(self, context):
        """Read-only report restricted to this account's subject assignments."""
        if self.role == 'admin':
            raise TimetableError('This report is for assigned teacher accounts only.')
        grade, section = context[3], context[4]
        grants = self.granted_subjects(grade, section)
        allowed = {str(subject).strip().casefold() for subject in grants}
        if not allowed:
            return 0, 0, 0
        scoped = self.store.scoped_load(context, grants)
        if not scoped:
            return 0, 0, 0
        # Conflict checking considers other saved classes, but the result does
        # not disclose their identities, teachers or unauthorized subjects.
        count = len(self.store.conflicts(context, scoped))
        missing = sum(1 for slot in scoped.values() if not (
            slot.teacher.strip() or self.store.teacher_for(grade, section, slot.subject)))
        return len(scoped), count, missing

    def check_assigned_clashes(self):
        try:
            context = self.context()
            lessons, collisions, unassigned = self.assigned_clash_report(context)
        except (TimetableError, ValueError) as err:
            messagebox.showerror('Clash check', str(err), parent=self.root)
            return
        if not lessons:
            message = ('No saved lessons for your assigned subjects in this selected week. '
                       'Try Find My Subject or ask the administrator to check subject assignments.')
        else:
            message = (f'Checked {lessons} authorized lesson(s).\n'
                       f'Possible teacher clashes: {collisions}.\n'
                       f'Lessons without a teacher assignment: {unassigned}.\n\n'
                       'This is a read-only check; your timetable was not changed. '
                       'Unassigned teachers cannot be checked.')
        messagebox.showinfo('My Subject • Clash Check', message, parent=self.root)

    def check_clashes(self):
        """Admins reshuffle; editors/viewers receive a read-only scoped clash report."""
        if self.role != 'admin':
            return self.check_assigned_clashes()
        """Smart reshuffle for the selected class, then check known clashes."""
        if not self.require_admin() or not self.guard_unsaved():return
        context=self.context()
        previous=dict(self.slots)
        try:
            # Try different arrangements; prefer the one that changes the
            # largest number of cells without breaking exact weekly quotas.
            candidate=None
            changed=-1
            for _ in range(5):
                proposal=self.scheduler.generate(context,variation=secrets.randbits(32))
                self.store.validate(context,proposal)
                difference=sum(previous.get(key,None)!=value for key,value in proposal.items())
                if difference>changed:
                    candidate=proposal
                    changed=difference
                if difference>=len(proposal)*0.65:
                    break
            if candidate is None:
                raise TimetableError('No clash-free timetable was found.')
            # Save before displaying. On failure the previous saved timetable
            # remains untouched, including custom edits.
            self.store.save(context,candidate)
            self.store.log(self.user,'Smart shuffled timetable '+':'.join(context))
        except (TimetableError,ValueError,Exception) as err:
            messagebox.showerror('Cannot rearrange',
                'The previous timetable was kept unchanged.\n\n'+str(err))
            return
        self.slots=candidate
        self.loaded_context=context
        self.dirty=False
        self.update_filter()
        self.draw()
        problems=self.all_saved_conflicts()
        unassigned=sorted({s.subject for s in self.slots.values() if s.subject!='---' and
                           not s.teacher and not self.store.teacher_for(self.grade_var.get(),self.section_var.get(),s.subject)})
        message=(f'Smart timetable rearranged and saved. {changed} period cells changed.\n'
                 'Subjects now vary across daily periods wherever the schedule permits.\n\n')
        if problems:
            message+='Existing saved teacher conflicts:\n'+'\n'.join(problems[:15])
        else:
            message+='No clashes detected among known teacher assignments.'
        if unassigned:
            message+='\n\nTeachers not assigned (cannot check): '+', '.join(unassigned)
        messagebox.showinfo('Smart Shuffle & Clash Check',message)

    def backup(self):
        if not self.require_admin():return
        path=filedialog.asksaveasfilename(defaultextension='.sqlite3',initialfile='APMHSS_Backup.sqlite3',filetypes=[('SQLite backup','*.sqlite3')])
        if not path:return
        try:self.store.backup(path)
        except Exception as err:messagebox.showerror('Backup failed',str(err));return
        messagebox.showinfo('Backup created','SQLite backup saved at:\n'+path)

    def manage_users(self):
        if not self.require_admin():return
        win=tk.Toplevel(self.root)
        win.title('Accounts and Teacher Subject Access')
        win.geometry('800x690');win.minsize(700,580)
        win.transient(self.root);win.grab_set()
        pane=ttk.Frame(win,padding=16);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text='ACCOUNTS • SUBJECT-LEVEL PERMISSIONS',font=('Segoe UI',15,'bold')).pack(anchor='w',pady=6)
        ttk.Label(pane,text='Viewers can see only assigned subjects. Editors may edit only their assigned subject blocks.').pack(anchor='w')
        new_frame=ttk.LabelFrame(pane,text='Create Admin / Editor / Viewer Login',padding=10)
        new_frame.pack(fill='x',pady=8)
        uname=tk.StringVar();password=tk.StringVar();role=tk.StringVar(value='viewer')
        for col,label,var in [(0,'Username',uname),(1,'Password',password)]:
            ttk.Label(new_frame,text=label).grid(row=0,column=col,sticky='w',padx=4)
            ttk.Entry(new_frame,textvariable=var,show='*' if col==1 else '').grid(row=1,column=col,sticky='ew',padx=4)
        ttk.Label(new_frame,text='Role').grid(row=0,column=2)
        ttk.Combobox(new_frame,textvariable=role,values=['viewer','editor','admin'],state='readonly',width=10).grid(row=1,column=2,padx=4)
        def usernames():
            return [row[0] for row in self.auth_store.db.execute(
                "SELECT username FROM users ORDER BY username")]
        chosen_user=tk.StringVar(value='')
        def create():
            try:self.auth_store.add_user(uname.get(),password.get(),role.get())
            except Exception as err:messagebox.showerror('User error',str(err),parent=win);return
            chosen_user.set(uname.get().strip().lower())
            user_combo.configure(values=usernames())
            self.auth_store.log(self.user,'Created teacher account '+chosen_user.get())
            if role.get() == 'admin':
                messagebox.showinfo('Administrator created',
                    'This account has full administrator access. Subject grants are not required.', parent=win)
            else:
                messagebox.showinfo('Created','Now grant this account access to its subjects below.',parent=win)
        ttk.Button(new_frame,text='Create Account',command=create).grid(row=1,column=3,padx=8)
        for col in (0,1):new_frame.grid_columnconfigure(col,weight=1)
        access=ttk.LabelFrame(pane,text='Grant / Revoke Access to ONE Subject',padding=10)
        access.pack(fill='x',pady=8)
        grade=tk.StringVar(value='12');section=tk.StringVar(value='A');subject=tk.StringVar(value='')
        ttk.Label(access,text='Teacher Account').grid(row=0,column=0,sticky='w')
        ttk.Label(access,text='Class').grid(row=0,column=1,sticky='w')
        ttk.Label(access,text='Section').grid(row=0,column=2,sticky='w')
        ttk.Label(access,text='Subject').grid(row=0,column=3,sticky='w')
        user_combo=ttk.Combobox(access,textvariable=chosen_user,values=usernames(),state='readonly',width=19)
        user_combo.grid(row=1,column=0,sticky='ew',padx=3)
        grade_cb=ttk.Combobox(access,textvariable=grade,values=list(GRADES),state='readonly',width=8)
        grade_cb.grid(row=1,column=1,padx=3)
        section_cb=ttk.Combobox(access,textvariable=section,values=self.auth_store.sections('12'),state='readonly',width=12)
        section_cb.grid(row=1,column=2,padx=3)
        subject_cb=ttk.Combobox(access,textvariable=subject,width=24)
        subject_cb.grid(row=1,column=3,sticky='ew',padx=3)
        access.grid_columnconfigure(0,weight=1);access.grid_columnconfigure(3,weight=1)
        permissions=ttk.Treeview(pane,columns=('Class','Section','Subject'),show='headings',height=10)
        for col in ('Class','Section','Subject'):
            permissions.heading(col,text=col)
            permissions.column(col,width=120 if col!='Subject' else 260)
        permissions.pack(fill='both',expand=True,pady=8)
        def refresh_grants(*_):
            permissions.delete(*permissions.get_children())
            for g,sec,sub in self.auth_store.grants(chosen_user.get()):
                permissions.insert('',tk.END,values=(g,sec,sub))
        def refresh_subjects(*_):
            secs=self.auth_store.sections(grade.get())
            section_cb.configure(values=secs)
            if section.get() not in secs:section.set(secs[0])
            mains,extras=self.auth_store.subjects(grade.get(),section.get())
            plan=self.auth_store.weekly_plan(grade.get(),section.get())
            subjects=list(dict.fromkeys(list(plan)+mains+extras))
            subject_cb.configure(values=subjects)
            if subject.get() not in subjects:subject.set(subjects[0] if subjects else '')
        grade_cb.bind('<<ComboboxSelected>>',refresh_subjects)
        section_cb.bind('<<ComboboxSelected>>',refresh_subjects)
        user_combo.bind('<<ComboboxSelected>>',refresh_grants)
        def grant():
            account=chosen_user.get().strip().lower()
            record=self.auth_store.db.execute('SELECT role FROM users WHERE username=?',(account,)).fetchone()
            if not record:
                messagebox.showwarning('Select account','Choose an existing account first.',parent=win);return
            if record['role']=='admin':
                messagebox.showinfo('Admin access','Administrators already have access to every subject.',parent=win)
                return
            try:
                self.auth_store.grant_subject(account,grade.get(),section.get(),subject.get())
            except Exception as err:
                messagebox.showerror('Grant failed',str(err),parent=win);return
            refresh_grants()
            # Automatically prepare the class timetable; no manual Save button.
            context=self._default_teacher_context(grade.get(),section.get())
            ok, explanation=self.auth_store.ensure_assigned_schedule(context,subject.get())
            self.auth_store.log(self.user,'Granted subject '+account+' '+grade.get()+'-'+section.get()+' '+subject.get())
            if ok:
                messagebox.showinfo('Access saved',
                    'Teacher permission saved to SQLite.\n'+explanation+'\n'
                    'The viewer/editor can now open Find My Subject.',parent=win)
            else:
                messagebox.showwarning('Access saved; timetable pending',
                    'Permission was saved to SQLite, but the lesson is not yet visible.\n\n'
                    +explanation+'\n\nExisting timetables were not overwritten.',parent=win)
        def revoke():
            selected=permissions.selection()
            if not selected:return
            g,sec,sub=permissions.item(selected[0],'values')
            self.auth_store.revoke_subject(chosen_user.get(),g,sec,sub)
            refresh_grants()
        buttons=ttk.Frame(pane);buttons.pack(fill='x',pady=6)
        ttk.Button(buttons,text='Grant Subject Access',command=grant).pack(side='left',padx=4)
        ttk.Button(buttons,text='Revoke Selected Access',command=revoke).pack(side='left',padx=4)
        ttk.Button(buttons,text='Close',command=win.destroy).pack(side='right',padx=4)
        refresh_subjects()
        if usernames():chosen_user.set(usernames()[0]);refresh_grants()

    def change_password(self):
        old=simpledialog.askstring('Verify identity','Enter your CURRENT password:',show='*',parent=self.root)
        if old is None:return
        if not self.auth_store.login(self.user,old):
            messagebox.showerror('Invalid password','Current password was incorrect.');return
        new_username=self.user
        if self.role=='admin':
            value=simpledialog.askstring('Change Admin Username',
                'New username (leave unchanged to keep current):',initialvalue=self.user,parent=self.root)
            if value is None:return
            new_username=value.strip().lower()
        new=simpledialog.askstring('Change Password',
            'New password (leave empty to keep your current password):',show='*',parent=self.root)
        if new is None:return
        if new:
            again=simpledialog.askstring('Confirm Password','Repeat the new password:',show='*',parent=self.root)
            if new!=again:
                messagebox.showerror('Mismatch','Passwords do not match.');return
        try:
            self.user=self.auth_store.update_credentials(self.user,new_username,new or None)
        except (TimetableError,Exception) as err:
            messagebox.showerror('Account update failed',str(err));return
        self.auth_store.log(self.user,'Account credentials updated')
        messagebox.showinfo('Account updated','Your username and/or password has been updated.')
        if self.mode=='edification':self.edification_screen()
        else:self.main_screen()

    def login_history(self):
        if self.role!='admin':messagebox.showwarning('Admin only','Only administrators can view login history.');return
        win=tk.Toplevel(self.root);win.title('Local Login History');win.geometry('650x430')
        frame=ttk.Frame(win,padding=12);frame.pack(fill='both',expand=True)
        rows=self.auth_store.db.execute('SELECT created_at,username,event FROM audit ORDER BY id DESC LIMIT 250').fetchall()
        tree=ttk.Treeview(frame,columns=('Time','User','Event'),show='headings')
        for c in ('Time','User','Event'):tree.heading(c,text=c);tree.column(c,width=190 if c!='Event' else 250)
        tree.pack(fill='both',expand=True)
        for row in rows:tree.insert('',tk.END,values=tuple(row))
        def clear_history():
            if self.role != 'admin':
                messagebox.showwarning('Restricted', 'Only administrators can clear login history.', parent=win)
                return
            if not messagebox.askyesno('Clear Login History',
                'Permanently delete all previous login and activity history?\n\n'
                'This cannot be undone.', parent=win):
                return
            self.auth_store.clear_history(self.user)
            for item in tree.get_children():tree.delete(item)
            for row in self.auth_store.db.execute(
                'SELECT created_at,username,event FROM audit ORDER BY id DESC LIMIT 250'):
                tree.insert('', tk.END, values=tuple(row))
            messagebox.showinfo('History cleared',
                'Previous history was deleted. A new audit entry records this action.', parent=win)
        ttk.Button(frame, text='Clear History (Admin Only)', command=clear_history).pack(
            anchor='e', pady=(10, 0))

    def logout(self):
        if not self.guard_unsaved():return
        self.auth_store.log(self.user,'Signed out')
        self.store=self.auth_store;self.mode='class';self.scheduler=Scheduler(self.store)
        self.user='';self.role='viewer';self.slots={};self.loaded_context=None
        self.login_screen()

    def exit_app(self):
        if not self.guard_unsaved():return
        if self._welcome_job is not None:
            try:self.root.after_cancel(self._welcome_job)
            except tk.TclError:pass
        if self.user:self.auth_store.log(self.user,'Exited application')
        if self.revision_store is not None:self.revision_store.close()
        self.auth_store.close();self.root.destroy()

if __name__=='__main__':
    root=tk.Tk()
    app=App(root)
    root.mainloop()
