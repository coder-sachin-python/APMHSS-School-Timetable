"""APMHSS Class Time Table Generator — improved classroom edition.

Run: python CLASS_TIME_TABLE_GENERATOR.py
Install: python -m pip install pillow python-docx reportlab openpyxl
The local SQLite database is created under the user's home folder.
"""
from __future__ import annotations
import calendar
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
                            PERIODS,group,school_days,UNIT_GRADES,unit_for_grade,weekly_capacity)
from timetable_export import EXPORTERS

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
MAROON='#7A0019';GOLD='#D4AF37';NAVY='#22334C';LIGHT='#EFF3F8';TEXT='#27384C'


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
        self.store=Store()
        self.scheduler=Scheduler(self.store)
        self.user='';self.role='viewer';self.login_failures=0
        self.slots={};self.dirty=False;self.loaded_context=None
        self.colors={}
        self.table_zoom=1.12  # Large, readable default timetable
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
        cv.coords(self._login_button_window, center, y + 538 * scale)
        cv.itemconfigure(self._login_button_window, width=field_width)
        cv.coords(self._login_setup_note, center, y + 589 * scale)
        cv.coords(self._login_motto_shadow, width // 2 + 2, height - 28)
        cv.coords(self._login_motto, width // 2, height - 30)

    def login_screen(self):
        """Original APMHSS full-photo login design with improved secure accounts."""
        self.reset_root()
        ttk.Button(self.root,text='Forgot password / recovery code',command=self.forgot_password).pack(side='bottom')
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
        self.user_field.insert(0, 'admin')
        self.pass_field = ttk.Entry(cv, font=('Segoe UI', 12), show='*')
        self.pass_field.bind('<Return>', lambda event: self.sign_in())
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

    def first_run(self):
        if self.store.has_users():return
        pw=simpledialog.askstring('First-time setup','Create ADMIN password (8+ characters, letters and numbers):',show='*',parent=self.root)
        if pw is None: self.root.destroy();return
        confirm=simpledialog.askstring('Confirm password','Enter the new admin password again:',show='*',parent=self.root)
        if confirm is None: self.root.destroy();return
        if pw!=confirm:
            messagebox.showerror('Password mismatch','Passwords did not match. Try again.')
            self.root.after(150,self.first_run);return
        try: self.store.add_user('admin',pw,'admin')
        except (TimetableError,Exception) as err:
            messagebox.showerror('Password requirements',str(err));self.root.after(150,self.first_run);return
        messagebox.showinfo('Administrator created','Admin username: admin\nUse your new password to sign in.')

    def forgot_password(self):
        username=simpledialog.askstring('Recover account','Username:',parent=self.root)
        if not username:return
        code=simpledialog.askstring('Recover account','One-use recovery code:',show='*',parent=self.root)
        if not code:return
        password=simpledialog.askstring('Recover account','New password (8+ characters, letters and numbers):',show='*',parent=self.root)
        if password is None:return
        try:
            ok=self.store.recover(username,code,password)
        except ValueError as error:
            messagebox.showerror('Recovery',str(error));return
        messagebox.showinfo('Recovery','Password changed.' if ok else 'Invalid code or recovery temporarily locked.')

    def create_recovery_codes(self):
        password=simpledialog.askstring('Recovery codes','Confirm your current password:',show='*',parent=self.root)
        if password is None or not self.store.login(self.user,password):
            messagebox.showerror('Recovery codes','Authentication failed.');return
        codes=self.store.recovery_codes(self.user)
        win=tk.Toplevel(self.root);win.title('Save recovery codes offline')
        ttk.Label(win,text='Each code works once. Creating codes invalidates previous codes.').pack(padx=15,pady=10)
        text=tk.Text(win,width=45,height=10);text.pack(padx=15,pady=10)
        text.insert('1.0','\n'.join(codes));text.configure(state='disabled')

    def sign_in(self):
        username=self.user_field.get().strip().lower()
        password=self.pass_field.get()
        if not self.store.has_users():
            messagebox.showwarning('Setup required','Create the admin account first.');self.first_run();return
        role=self.store.login(username,password)
        self.pass_field.delete(0,'end')
        if not role:
            self.login_failures+=1
            self.store.log(username or '(blank)','Failed login')
            messagebox.showerror('Login failed','Incorrect credentials or account temporarily locked. After repeated failures, wait 15 minutes.')
            return
        self.user=username;self.role=role
        self.store.log(username,'Signed in')
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
            self.main_screen()

        animate()

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
        n=len(self.store.layout(grade,self.section_var.get())[1])
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
        self.draw()

    def fit_table_width(self):
        if not hasattr(self,'canvas') or not self.canvas.winfo_exists():return
        n=len(self.store.layout(self.grade_var.get(),self.section_var.get())[1])
        available=max(600,self.canvas.winfo_width())
        self.table_zoom=round(min(1.45,max(.85,(available-140)/(150*n))),2)
        self.draw()

    def can_edit(self): return self.role in ('admin','editor')
    def require_edit(self):
        if self.can_edit(): return True
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
        tk.Label(text,text='REVISION TIME TABLE GENERATOR • MELMARUVATHUR',font=('Segoe UI',10),fg='#F1D394',bg=MAROON).pack(anchor='w')
        tk.Label(header,text=f'{self.user} ({self.role})',bg=MAROON,fg='white').pack(side='right',padx=18)

        controls=tk.Frame(self.root,bg='white',padx=10,pady=9)
        controls.pack(fill='x',padx=10,pady=(10,4))
        self.year_var=tk.StringVar(value='2026-2027')
        self.month_var=tk.StringVar(value=calendar.month_name[datetime.now().month])
        self.week_var=tk.StringVar(value='Week 1')
        self.grade_var=tk.StringVar(value='8')
        self.section_var=tk.StringVar(value='A')
        self.students_var=tk.StringVar(value='40')
        self.filter_var=tk.StringVar(value='All Subjects')
        fields=[('Academic Year',self.year_var,['2025-2026','2026-2027','2027-2028']),
                ('Month',self.month_var,list(calendar.month_name)[1:]),
                ('Week',self.week_var,[f'Week {i}' for i in range(1,6)]),
                ('Class',self.grade_var,list(GRADES)),
                ('Section',self.section_var,self.store.sections('8'))]
        self.cbs={}
        for col,(label,var,values) in enumerate(fields):
            tk.Label(controls,text=label,bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=col,sticky='w',padx=5)
            cb=ttk.Combobox(controls,textvariable=var,values=values,state='readonly',width=17 if col!=3 else 9)
            cb.grid(row=1,column=col,sticky='ew',padx=5,pady=4)
            cb.bind('<<ComboboxSelected>>',lambda e:self.selection_change())
            self.cbs[label]=cb
        tk.Label(controls,text='Students',bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=5,sticky='w',padx=5)
        st=ttk.Entry(controls,textvariable=self.students_var,width=8)
        st.grid(row=1,column=5,padx=5,pady=4,sticky='ew')
        ttk.Button(controls,text='Save Count',command=self.save_students).grid(row=1,column=6,padx=4)
        tk.Label(controls,text='Subject Filter',bg='white',fg=NAVY,font=('Segoe UI',9,'bold')).grid(row=0,column=7,sticky='w',padx=4)
        self.filter_cb=ttk.Combobox(controls,textvariable=self.filter_var,values=['All Subjects'],state='readonly',width=17)
        self.filter_cb.grid(row=1,column=7,padx=4,sticky='ew')
        self.filter_cb.bind('<<ComboboxSelected>>',lambda e:self.draw())
        for c in range(8):controls.grid_columnconfigure(c,weight=1)

        toolbar=tk.Frame(self.root,bg=LIGHT)
        toolbar.pack(fill='x',padx=11,pady=(3,4))
        actions=[('Save Progress',self.save),('Load Saved',self.load),
                 ('Export Word',lambda:self.export('Word (.docx)')),
                 ('Export PDF',lambda:self.export('PDF (.pdf)')),
                 ('Export Excel',lambda:self.export('Excel (.xlsx)')),
                 ('Teachers',self.teacher_dashboard),('Subjects',self.subject_rules)]
        row1=tk.Frame(toolbar,bg=LIGHT);row1.pack(fill='x')
        for title,callback in actions:
            ttk.Button(row1,text=title,command=callback).pack(side='left',padx=2,pady=2)
        row2=tk.Frame(toolbar,bg=LIGHT);row2.pack(fill='x')
        for title,callback in [('Unit Dashboard',self.unit_dashboard),('Add Section',self.add_section),('Delete Section',self.delete_section),
                               ('Check Clashes',self.check_clashes),('Period Timing / Layout',self.period_layout_editor),('Backup Data',self.backup),
                               ('Accounts',self.manage_users),('Change Password',self.change_password),
                               ('Recovery Codes',self.create_recovery_codes),('Login History',self.login_history),('Logout',self.logout)]:
            ttk.Button(row2,text=title,command=callback).pack(side='left',padx=2,pady=2)

        info_bar=tk.Frame(self.root,bg=LIGHT)
        info_bar.pack(fill='x',padx=14,pady=(6,4))
        self.summary=tk.Label(info_bar,text='Timetables are generated automatically.',bg=LIGHT,fg=MAROON,
                              font=('Segoe UI',12,'bold'),anchor='w')
        self.summary.pack(side='left',fill='x',expand=True)
        tk.Label(info_bar,text='TIMETABLE SIZE',bg=LIGHT,fg=NAVY,
                 font=('Segoe UI',9,'bold')).pack(side='left',padx=(10,5))
        ttk.Button(info_bar,text='A−',width=4,command=lambda:self.change_table_zoom(-.12)).pack(side='left',padx=2)
        ttk.Button(info_bar,text='A+',width=4,command=lambda:self.change_table_zoom(.12)).pack(side='left',padx=2)
        ttk.Button(info_bar,text='Fit Width',command=self.fit_table_width).pack(side='left',padx=(3,0))
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
        self.status=tk.Label(self.root,text='Double-click a period cell to edit. Teacher checks require teacher assignments.',
                             bg='#DDE7F0',fg=NAVY,anchor='w',pady=7)
        self.status.pack(fill='x')
        self.selection_change(force=True)
        self.root.after(180,self.fit_table_width)

    def context(self):
        return (self.year_var.get(),self.month_var.get(),self.week_var.get(),self.grade_var.get(),self.section_var.get())

    def guard_unsaved(self):
        if not self.dirty:return True
        response=messagebox.askyesnocancel('Unsaved changes','Save your current timetable before changing selection?')
        if response is None: return False
        if response and not self.save(): return False
        self.dirty=False
        return True

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
        sections=self.store.sections(grade)
        self.cbs['Section']['values']=sections
        if self.section_var.get() not in sections: self.section_var.set(sections[0])
        self.students_var.set(str(self.store.students(grade,self.section_var.get())))
        self.load(silent=True)

    def load(self,silent=False):
        if not silent and not self.guard_unsaved(): return
        ctx=self.context()
        self.slots=self.store.load(ctx)
        auto_error=None
        if not self.slots and self.can_edit():
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
            messagebox.showinfo('Loaded',f'{len(self.slots)} timetable cells loaded from SQLite.')

    def update_filter(self):
        subjects=sorted(set(s.subject for s in self.slots.values()))
        self.filter_cb['values']=['All Subjects']+subjects
        if self.filter_var.get() not in self.filter_cb['values']: self.filter_var.set('All Subjects')

    def generate(self):
        if not self.require_edit() or not self.guard_unsaved():return
        try:
            new=self.scheduler.generate(self.context())
            self.store.validate(self.context(),new)
        except (TimetableError,ValueError) as err:
            messagebox.showerror('Cannot generate',str(err));return
        self.slots=new;self.loaded_context=self.context();self.dirty=True
        self.update_filter();self.draw()
        self.status.config(text='Generated preview — click Save Progress to save permanently.')

    def draw(self):
        """Large responsive timetable: maximized view, zoom and both scrollbars."""
        for child in self.grid_frame.winfo_children(): child.destroy()
        year,month,week,grade,section=self.context()
        days, times = self.store.layout(grade,section)
        zoom=self.table_zoom
        self._table_period_cells=[]
        day_width=round(136*zoom)
        period_width=round(150*zoom)
        header_height=round(82*zoom)
        row_height=round(98*zoom)
        for col in range(len(times)+1):
            self.grid_frame.grid_columnconfigure(col,weight=1,
                  minsize=day_width if col==0 else period_width)
        self.grid_frame.grid_rowconfigure(0,weight=0,minsize=header_height)
        headers=['DAY']+[f'PERIOD {i+1}\n{a} - {b}' for i,(a,b) in enumerate(times)]
        for col,heading in enumerate(headers):
            tk.Label(self.grid_frame,text=heading,font=('Segoe UI',max(10,round(11*zoom)),'bold'),
                     bg=MAROON,fg='white',width=1,justify='center',wraplength=period_width-16,
                     padx=8,pady=12,bd=1,relief='solid').grid(row=0,column=col,sticky='nsew')
        count=Counter(x.subject for x in self.slots.values())
        for row,day in enumerate(days,1):
            self.grid_frame.grid_rowconfigure(row,weight=1,minsize=row_height)
            tk.Label(self.grid_frame,text=day,font=('Segoe UI',max(11,round(12*zoom)),'bold'),
                     bg='#F5EACE',fg=MAROON,width=1,padx=8,pady=14,
                     bd=1,relief='solid').grid(row=row,column=0,sticky='nsew')
            for p in range(len(times)):
                slot=self.slots.get((day,p))
                subject=slot.subject if slot else '---'
                teacher=(slot.teacher or self.store.teacher_for(grade,section,subject)) if slot else ''
                show=subject+(f'\n{teacher}' if teacher else '')
                dim=self.filter_var.get() not in ('All Subjects',subject)
                bg='#F0F2F5' if dim else (slot.bg if slot else 'white')
                fg='#ADB4BE' if dim else (slot.fg if slot else TEXT)
                lab=tk.Label(self.grid_frame,text=show,width=1,wraplength=period_width-20,
                             justify='center',font=('Segoe UI',max(10,round(11*zoom)),'bold'),
                             bg=bg,fg=fg,padx=7,pady=15,bd=1,relief='solid',
                             cursor='hand2' if self.can_edit() else 'arrow')
                lab.grid(row=row,column=p+1,sticky='nsew')
                lab.bind('<Double-Button-1>',lambda event,d=day,i=p:self.edit_cell(d,i))
                self._table_period_cells.append(lab)
        name=f'CLASS {grade}-{section} | {week} | {month} {year} | Students: {self.students_var.get()}'
        name+=f" | {'UNSAVED CHANGES' if self.dirty else 'SAVED' if self.slots else 'NOT GENERATED'}"
        self.summary.config(text=name)
        self._fit_grid_to_canvas()
        self.status.config(text=('Weekly periods: '+' | '.join(f'{s}={n}' for s,n in sorted(count.items())))[:260])

    def edit_cell(self,day,p):
        if not self.require_edit() or not self.slots: return
        grade=self.grade_var.get();section=self.section_var.get()
        old=self.slots.get((day,p),Slot(day,p,'---'))
        win=tk.Toplevel(self.root);win.title(f'Edit: {day}, period {p+1}');win.geometry('430x360');win.transient(self.root);win.grab_set()
        panel=ttk.Frame(win,padding=20);panel.pack(fill='both',expand=True)
        subject=text_entry(panel,'Subject or activity',default=old.subject)
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
            updated=replace(old,subject=s,teacher=teacher.get().strip(),bg=chosen[0],fg=chosen[1])
            copy=dict(self.slots);copy[(day,p)]=updated
            try:self.store.validate(self.context(),copy)
            except TimetableError as err:
                messagebox.showerror('Teacher Clash',str(err),parent=win);return
            self.slots=copy;self.dirty=True;self.update_filter();self.draw();win.destroy()
        ttk.Button(panel,text='Apply edit',command=apply).pack(pady=8,fill='x')

    def save(self):
        if not self.require_edit():return False
        if not self.slots:
            messagebox.showwarning('Nothing to save','No timetable available. Check the Unit Dashboard and teacher assignments.');return False
        try:
            self.store.save(self.context(),self.slots)
            self.store.set_students(self.grade_var.get(),self.section_var.get(),self.students_var.get())
            self.store.log(self.user,'Saved timetable '+':'.join(self.context()))
        except (TimetableError,ValueError,Exception) as err:
            messagebox.showerror('Save failed',str(err));return False
        self.dirty=False;self.loaded_context=self.context();self.draw()
        messagebox.showinfo('Saved','The timetable was saved permanently to the SQLite database.')
        return True

    def save_students(self):
        if not self.require_edit():return
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
        if not self.require_edit() or not self.guard_unsaved():return
        value=simpledialog.askstring('Add Section','Enter section name (e.g. D, B3):',parent=self.root)
        if value is None:return
        try:self.store.add_section(self.grade_var.get(),value)
        except Exception as err:messagebox.showerror('Cannot add',str(err));return
        self.selection_change(force=True)
        self.cbs['Section']['values']=self.store.sections(self.grade_var.get())
        self.section_var.set(value.strip().upper());self.selection_change(force=True)

    def delete_section(self):
        if not self.require_edit() or not self.guard_unsaved():return
        grade=self.grade_var.get();sec=self.section_var.get()
        if not messagebox.askyesno('Delete Section',f'Delete {grade}-{sec} and its saved timetables and teacher assignments?'):return
        if not self.guard_unsaved():return
        try:self.store.delete_section(grade,sec)
        except Exception as err:messagebox.showerror('Cannot delete',str(err));return
        self.selection_change(force=True)

    def subject_rules(self):
        if not self.require_edit():return
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

    def unit_dashboard(self):
        """Manage weekly subject quotas for every grade and section in five units."""
        if not self.require_edit():return
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
        def save_plan():
            # Do not silently overwrite unsaved manual timetable edits.
            if self.dirty and not self.guard_unsaved():
                load_plan()
                return False
            if not draft:
                messagebox.showwarning('No subjects','Add at least one subject.',parent=win)
                return False
            targets=self.store.sections(grade.get()) if all_sections.get() else [section.get()]
            if all_sections.get() and len(targets)>1:
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
        ttk.Checkbutton(outer,text='Apply this plan to all sections of the selected class',
                        variable=all_sections).pack(anchor='w',pady=(5,4))
        bottom=ttk.Frame(outer);bottom.pack(fill='x',pady=(4,0))
        ttk.Label(bottom,text='AUTO-SAVE ON: Add / Update / Remove automatically refreshes timetables.',
                  foreground='#187245').pack(side='left',padx=(0,8))
        ttk.Button(bottom,text='Close',command=lambda:close_window()).pack(side='left')
        ttk.Label(bottom,text='Example: Tamil = 5 periods per week',foreground='#53657B').pack(side='right')
        def close_window():
            if discard_changes():win.destroy()
        win.protocol('WM_DELETE_WINDOW',close_window)
        state['last_grade']=grade.get();state['last_section']=section.get()
        load_plan()

    def period_layout_editor(self):
        """Edit rows, columns and 24-hour times for the selected class/section."""
        if not self.require_edit():
            return
        if not self.guard_unsaved():
            return
        grade, section = self.grade_var.get(), self.section_var.get()
        days, times = self.store.layout(grade, section)
        win = tk.Toplevel(self.root)
        win.title(f'PERIOD TIMINGS & ROWS / COLUMNS | Class {grade}-{section}')
        win.geometry('720x690')
        win.minsize(620, 500)
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'EDIT PERIOD TIMINGS | CLASS {grade}-{section}',
                  font=('Segoe UI', 17, 'bold')).pack(anchor='w', pady=(0, 8))
        ttk.Label(frame, text='Rows = Monday onward (1-6 school days). Columns = periods per day (1-12).',
                  wraplength=650).pack(anchor='w')
        ttk.Label(frame, text='Use 24-hour clock: 08:45, 12:30, 13:00, 16:30. Breaks between periods are allowed.',
                  wraplength=650).pack(anchor='w', pady=(2, 12))
        controls = ttk.Frame(frame)
        controls.pack(fill='x', pady=5)
        day_var = tk.StringVar(value=str(len(days)))
        period_var = tk.StringVar(value=str(len(times)))
        ttk.Label(controls, text='Rows / School days').pack(side='left')
        ttk.Spinbox(controls, from_=1, to=6, width=6, textvariable=day_var).pack(side='left', padx=(8, 20))
        ttk.Label(controls, text='Columns / Periods per day').pack(side='left')
        ttk.Spinbox(controls, from_=1, to=12, width=6, textvariable=period_var).pack(side='left', padx=8)

        content = ttk.Frame(frame)
        content.pack(fill='both', expand=True, pady=(10, 4))
        canvas = tk.Canvas(content, bg='white', highlightthickness=0)
        scroll = ttk.Scrollbar(content, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        grid = ttk.Frame(canvas, padding=10)
        grid_window = canvas.create_window((0, 0), anchor='nw', window=grid)
        grid.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(grid_window, width=e.width))
        entries = []

        def update_fields():
            try:
                count = int(period_var.get())
                rows = int(day_var.get())
                if not 1 <= count <= 12 or not 1 <= rows <= 6:
                    raise ValueError
            except ValueError:
                messagebox.showwarning('Rows and columns', 'Choose 1-6 days and 1-12 periods.', parent=win)
                return
            previous = [(a.get().strip(), b.get().strip()) for a, b in entries]
            if not previous:
                previous = list(times)
            for widget in grid.winfo_children():
                widget.destroy()
            entries.clear()
            ttk.Label(grid, text='PERIOD', font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, padx=10, pady=6)
            ttk.Label(grid, text='START (HH:MM)', font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, padx=10)
            ttk.Label(grid, text='END (HH:MM)', font=('Segoe UI', 10, 'bold')).grid(row=0, column=2, padx=10)
            for index in range(count):
                if index < len(previous):
                    start, end = previous[index]
                else:
                    # New columns receive suggested times; you may edit them.
                    try:
                        last_end = self.store._clock_minutes(previous[-1][1])
                        start_min = last_end + 10
                        end_min = start_min + 40
                        start = f'{start_min//60:02d}:{start_min%60:02d}'
                        end = f'{end_min//60:02d}:{end_min%60:02d}'
                    except (IndexError, ValueError, TimetableError):
                        start, end = '09:00', '09:40'
                    previous.append((start, end))
                ttk.Label(grid, text=f'Period {index+1}').grid(row=index+1, column=0, padx=10, pady=8)
                a, b = tk.StringVar(value=start), tk.StringVar(value=end)
                ttk.Entry(grid, textvariable=a, width=18).grid(row=index+1, column=1, padx=10)
                ttk.Entry(grid, textvariable=b, width=18).grid(row=index+1, column=2, padx=10)
                entries.append((a, b))
            ttk.Label(grid, text=f'New weekly capacity: {rows * count} periods',
                      font=('Segoe UI', 10, 'bold')).grid(row=count+1, column=0, columnspan=3, pady=15)

        ttk.Button(controls, text='Apply Rows / Columns', command=update_fields).pack(side='left', padx=6)
        update_fields()

        def save_layout():
            try:
                rows = int(day_var.get())
                count = int(period_var.get())
                if count != len(entries):
                    messagebox.showwarning('Apply first', 'Click Apply Rows / Columns before saving.', parent=win)
                    return
                new_times = [(a.get().strip(), b.get().strip()) for a, b in entries]
                self.store.validate_layout(rows, new_times)
                if not messagebox.askyesno('Save and update timetable',
                    f'Save {rows} school days and {count} periods per day for Class {grade}-{section}?\n\n'
                    'This will automatically regenerate saved timetables for this section. '
                    'Other classes and sections will not be changed.', parent=win):
                    return
                updated = self.store.apply_layout_and_regenerate(
                    grade, section, rows, new_times, self.context())
                self.store.log(self.user, f'Updated layout for {grade}-{section}: {rows}x{count}')
            except (ValueError, TimetableError, Exception) as error:
                messagebox.showerror('Cannot save layout',
                    'The previous layout and timetables were kept.\n\n' + str(error), parent=win)
                return
            win.destroy()
            self.load(silent=True)
            self.root.after(80, self.fit_table_width)
            messagebox.showinfo('Layout saved',
                f'Class {grade}-{section} now has {rows} rows and {count} period columns.\n'
                f'Updated {updated} timetable(s). Settings are saved in the portable database.')

        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=8)
        ttk.Button(buttons, text='SAVE TIMINGS & UPDATE TIMETABLES',
                   command=save_layout).pack(side='left', padx=(0, 10))
        ttk.Button(buttons, text='Cancel', command=win.destroy).pack(side='left')

    def teacher_dashboard(self):
        if not self.require_edit():return
        win=tk.Toplevel(self.root);win.title('Teacher and Subject Dashboard');win.geometry('850x530');win.transient(self.root)
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
        columns=['Class','Section','Subject','Teacher']
        table=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse')
        for col in columns:table.heading(col,text=col);table.column(col,width=110 if col!='Teacher' else 210)
        table.pack(fill='both',expand=True,pady=12)
        def refresh_choices(*args):
            secs=self.store.sections(g.get());section_cb['values']=secs
            if sec.get() not in secs: sec.set(secs[0])
            main,extra=self.store.subjects(g.get(),sec.get())
            subject_cb['values']=sorted(set(main+extra+(['SKKA'] if g.get() in ('6','7','8') else [])))
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

    def check_clashes(self):
        """Smart reshuffle for the selected class, then check known clashes."""
        if not self.require_edit() or not self.guard_unsaved():return
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
        if not self.require_edit():return
        path=filedialog.asksaveasfilename(defaultextension='.sqlite3',initialfile='APMHSS_Backup.sqlite3',filetypes=[('SQLite backup','*.sqlite3')])
        if not path:return
        try:self.store.backup(path)
        except Exception as err:messagebox.showerror('Backup failed',str(err));return
        messagebox.showinfo('Backup created','SQLite backup saved at:\n'+path)

    def manage_users(self):
        if self.role!='admin':messagebox.showwarning('Admin only','Only the administrator can add users.');return
        win=tk.Toplevel(self.root);win.title('Add User Account');win.geometry('420x380');win.transient(self.root);win.grab_set()
        pane=ttk.Frame(win,padding=22);pane.pack(fill='both',expand=True)
        uname=text_entry(pane,'New username')
        password=text_entry(pane,'Password (8+ characters, letters and numbers)',show='*')
        role=tk.StringVar(value='viewer')
        ttk.Label(pane,text='Role').pack(anchor='w',pady=(12,0))
        ttk.Combobox(pane,textvariable=role,values=['viewer','editor','admin'],state='readonly').pack(fill='x')
        def create():
            try:self.store.add_user(uname.get(),password.get(),role.get())
            except Exception as err:messagebox.showerror('User error',str(err),parent=win);return
            self.store.log(self.user,'Created user '+uname.get().strip())
            messagebox.showinfo('Success','New user account created.',parent=win);win.destroy()
        ttk.Button(pane,text='Create User',command=create).pack(pady=20,fill='x')

    def change_password(self):
        old=simpledialog.askstring('Current password','Enter your existing password:',show='*',parent=self.root)
        if old is None:return
        if not self.store.login(self.user,old):
            messagebox.showerror('Invalid password','Current password was incorrect.');return
        new=simpledialog.askstring('New password','Choose a new password (8+ characters with letters and numbers):',show='*',parent=self.root)
        if new is None:return
        again=simpledialog.askstring('Confirm new password','Enter new password again:',show='*',parent=self.root)
        if new!=again:messagebox.showerror('Mismatch','Passwords do not match.');return
        try:self.store.set_password(self.user,new)
        except TimetableError as err:messagebox.showerror('Invalid password',str(err));return
        self.store.log(self.user,'Password changed')
        messagebox.showinfo('Password updated','Your new password is ready.')

    def login_history(self):
        if self.role!='admin':messagebox.showwarning('Admin only','Only administrators can view login history.');return
        win=tk.Toplevel(self.root);win.title('Local Login History');win.geometry('650x430')
        frame=ttk.Frame(win,padding=12);frame.pack(fill='both',expand=True)
        rows=self.store.db.execute('SELECT created_at,username,event FROM audit ORDER BY id DESC LIMIT 250').fetchall()
        tree=ttk.Treeview(frame,columns=('Time','User','Event'),show='headings')
        for c in ('Time','User','Event'):tree.heading(c,text=c);tree.column(c,width=190 if c!='Event' else 250)
        tree.pack(fill='both',expand=True)
        for row in rows:tree.insert('',tk.END,values=tuple(row))

    def logout(self):
        if not self.guard_unsaved():return
        self.store.log(self.user,'Signed out')
        self.user='';self.role='viewer';self.slots={};self.loaded_context=None
        self.login_screen()

    def exit_app(self):
        if not self.guard_unsaved():return
        if self._welcome_job is not None:
            try:self.root.after_cancel(self._welcome_job)
            except tk.TclError:pass
        if self.user:self.store.log(self.user,'Exited application')
        self.store.close();self.root.destroy()

if __name__=='__main__':
    if '--smoke-test' in sys.argv:
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            os.environ['APMHSS_DB_PATH']=str(Path(temporary)/'smoke.sqlite3')
            os.environ.pop('APMHSS_DATA_KEY',None)
            root=tk.Tk()
            app=App(root)
            root.update_idletasks()
            app.store.close()
            root.destroy()
    else:
        root=tk.Tk()
        app=App(root)
        root.mainloop()
