; Chromium carries empty runtime directories that Tauri's generated NSIS
; uninstaller can leave behind after every packaged file has been removed.
; Remove only the known empty directories, non-recursively. Abort this cleanup
; if the install root or any browser ancestor was replaced by a reparse point.
!include "LogicLib.nsh"

!define EBVS_FILE_ATTRIBUTE_REPARSE_POINT 0x400

!macro EBVS_ABORT_IF_REPARSE PATH
  System::Call 'kernel32::GetFileAttributesW(w "${PATH}") i .r0'
  ${If} $0 != -1
    IntOp $0 $0 & ${EBVS_FILE_ATTRIBUTE_REPARSE_POINT}
    ${If} $0 != 0
      Goto ebvs_browser_cleanup_done
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  !insertmacro EBVS_ABORT_IF_REPARSE "$INSTDIR"
  !insertmacro EBVS_ABORT_IF_REPARSE "$INSTDIR\embedded-browser"
  !insertmacro EBVS_ABORT_IF_REPARSE "$INSTDIR\embedded-browser\chrome-win64"
  !insertmacro EBVS_ABORT_IF_REPARSE "$INSTDIR\embedded-browser\chrome-win64\Dictionaries"
  RMDir "$INSTDIR\embedded-browser\chrome-win64\Dictionaries"
  RMDir "$INSTDIR\embedded-browser\chrome-win64"
  RMDir "$INSTDIR\embedded-browser"
  RMDir "$INSTDIR"
ebvs_browser_cleanup_done:
!macroend
