; Hooks for Tauri's NSIS installer (tauri.windows.conf.json).

; An update from WinSparkle starts the installer with /UPDATE while DataFluss is still closing. Its
; sidecars end a moment after the app; until then their files cannot be replaced. So wait up to ten
; seconds until the sidecar program can be opened for writing.
!macro NSIS_HOOK_PREINSTALL
  ${If} $UpdateMode = 1
    StrCpy $R9 0
    ${Do}
      ${IfNot} ${FileExists} "$INSTDIR\sidecar\schule-sidecar.exe"
        ${Break}
      ${EndIf}
      ClearErrors
      FileOpen $R8 "$INSTDIR\sidecar\schule-sidecar.exe" a
      ${IfNot} ${Errors}
        FileClose $R8
        ${Break}
      ${EndIf}
      IntOp $R9 $R9 + 1
      ${If} $R9 >= 50
        ${Break}
      ${EndIf}
      Sleep 200
    ${Loop}
  ${EndIf}
!macroend
