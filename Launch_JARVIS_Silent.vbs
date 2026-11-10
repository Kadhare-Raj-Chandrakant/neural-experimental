Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Kirito\OneDrive\Desktop\NeuraExperiment\Mark-L-main"
WshShell.Run """C:\Users\Kirito\AppData\Local\Programs\Python\Python311\pythonw.exe"" main.py", 0, False

