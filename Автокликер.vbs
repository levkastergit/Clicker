' Запуск GUI без окна CMD
Option Explicit

Dim fso, shell, folder, script, pythonw, pyw, cmd, errNum

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder

script = folder & "\clicker.pyw"
If Not fso.FileExists(script) Then
    MsgBox "Не найден файл clicker.pyw", vbCritical, "Автокликер"
    WScript.Quit 1
End If

pythonw = FindPythonw()
If pythonw = "" Then
    MsgBox "Python не найден. Установите Python с python.org и отметьте ""Add to PATH"".", vbCritical, "Автокликер"
    WScript.Quit 1
End If

cmd = """" & pythonw & """ """ & script & """"
shell.Run cmd, 0, False

Function FindPythonw()
    Dim candidates, c, pathEnv, parts, p, testPath
    FindPythonw = ""

    On Error Resume Next
    shell.Run "pythonw.exe /?", 0, True
    If Err.Number = 0 Then
        FindPythonw = "pythonw.exe"
        Err.Clear
        Exit Function
    End If
    Err.Clear

    shell.Run "pyw.exe -3 /?", 0, True
    If Err.Number = 0 Then
        FindPythonw = "pyw.exe -3"
        Err.Clear
        Exit Function
    End If
    Err.Clear
    On Error GoTo 0

    candidates = Array( _
        shell.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python313\pythonw.exe"), _
        shell.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python312\pythonw.exe"), _
        shell.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python311\pythonw.exe"), _
        "C:\Python313\pythonw.exe", _
        "C:\Python312\pythonw.exe" _
    )

    For Each c In candidates
        If fso.FileExists(c) Then
            FindPythonw = c
            Exit Function
        End If
    Next

    pathEnv = shell.ExpandEnvironmentStrings("%PATH%")
    parts = Split(pathEnv, ";")
    For Each p In parts
        testPath = p & "\pythonw.exe"
        If fso.FileExists(testPath) Then
            FindPythonw = testPath
            Exit Function
        End If
    Next
End Function
