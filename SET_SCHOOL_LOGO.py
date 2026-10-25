"""Optional: choose a new school logo before building the EXE.
Only changes school_logo.png and the .ico file; never changes project Python code.
"""
from pathlib import Path
from tkinter import Tk, filedialog, messagebox
from PIL import Image, ImageOps

folder = Path(__file__).resolve().parent
root = Tk()
root.withdraw()
source = filedialog.askopenfilename(
    title="Choose your school logo (PNG / JPG / WEBP)",
    filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
)
if source:
    try:
        with Image.open(source) as original:
            image = original.convert("RGBA")
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        image.save(folder / "school_logo.png", "PNG")
        icon_image = ImageOps.contain(image, (256, 256), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        canvas.alpha_composite(icon_image, ((256-icon_image.width)//2, (256-icon_image.height)//2))
        canvas.save(
            folder / "Adhiparasakthi_Time_Table_Icon.ico",
            format="ICO", sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]
        )
        messagebox.showinfo("Logo Updated", "Logo saved for the login page and Windows app icon.\n\nRun BUILD_WINDOWS_APP.bat to build the new EXE.")
    except Exception as exc:
        messagebox.showerror("Logo Error", f"Could not process this image:\n{exc}")
        raise
root.destroy()
