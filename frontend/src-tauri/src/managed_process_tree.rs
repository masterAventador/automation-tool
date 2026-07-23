//! Cross-platform ownership and forced cleanup for one spawned process tree.

#[cfg(unix)]
use std::io;
use std::process::{Child, Command};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ManagedProcessTreeError;

pub(crate) struct ManagedProcessTree {
    termination_requested: bool,
    #[cfg(unix)]
    process_group_id: i32,
    #[cfg(windows)]
    job: WindowsJob,
}

impl ManagedProcessTree {
    #[cfg(unix)]
    pub(crate) fn attach(child: &Child) -> Result<Self, ManagedProcessTreeError> {
        let process_group_id = i32::try_from(child.id()).map_err(|_| ManagedProcessTreeError)?;
        if process_group_id <= 0 {
            return Err(ManagedProcessTreeError);
        }
        Ok(Self {
            termination_requested: false,
            process_group_id,
        })
    }

    #[cfg(windows)]
    pub(crate) fn attach(child: &Child) -> Result<Self, ManagedProcessTreeError> {
        Ok(Self {
            termination_requested: false,
            job: WindowsJob::attach(child)?,
        })
    }

    #[cfg(all(not(unix), not(windows)))]
    pub(crate) fn attach(_child: &Child) -> Result<Self, ManagedProcessTreeError> {
        Err(ManagedProcessTreeError)
    }

    #[cfg(unix)]
    pub(crate) fn terminate(&mut self) -> Result<(), ManagedProcessTreeError> {
        if self.termination_requested {
            return Ok(());
        }
        let result = unsafe { libc::kill(-self.process_group_id, libc::SIGKILL) };
        if result == 0 || io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            self.termination_requested = true;
            Ok(())
        } else {
            Err(ManagedProcessTreeError)
        }
    }

    #[cfg(windows)]
    pub(crate) fn terminate(&mut self) -> Result<(), ManagedProcessTreeError> {
        if self.termination_requested {
            return Ok(());
        }
        self.job.terminate()?;
        self.termination_requested = true;
        Ok(())
    }

    #[cfg(all(not(unix), not(windows)))]
    pub(crate) fn terminate(&mut self) -> Result<(), ManagedProcessTreeError> {
        Err(ManagedProcessTreeError)
    }
}

#[cfg(unix)]
pub(crate) fn configure_managed_process(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    command.process_group(0);
}

#[cfg(windows)]
pub(crate) fn configure_managed_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_SUSPENDED;

    command.creation_flags(CREATE_SUSPENDED);
}

#[cfg(all(not(unix), not(windows)))]
pub(crate) fn configure_managed_process(_command: &mut Command) {}

#[cfg(windows)]
struct WindowsJob {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
unsafe impl Send for WindowsJob {}

#[cfg(windows)]
impl WindowsJob {
    fn attach(child: &Child) -> Result<Self, ManagedProcessTreeError> {
        use std::mem::{size_of, zeroed};
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(ManagedProcessTreeError);
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        let assigned = configured != FALSE
            && unsafe { AssignProcessToJobObject(handle, child.as_raw_handle() as _) } != FALSE;
        if !assigned || resume_suspended_process(child.id()).is_err() {
            unsafe {
                windows_sys::Win32::System::JobObjects::TerminateJobObject(handle, 1);
                CloseHandle(handle);
            }
            return Err(ManagedProcessTreeError);
        }
        Ok(Self { handle })
    }

    fn terminate(&mut self) -> Result<(), ManagedProcessTreeError> {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;

        if self.handle.is_null() {
            return Ok(());
        }
        let terminated = unsafe { TerminateJobObject(self.handle, 1) } != 0;
        let closed = unsafe { CloseHandle(self.handle) } != 0;
        self.handle = std::ptr::null_mut();
        if terminated || closed {
            Ok(())
        } else {
            Err(ManagedProcessTreeError)
        }
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.handle);
            }
            self.handle = std::ptr::null_mut();
        }
    }
}

#[cfg(windows)]
fn resume_suspended_process(process_id: u32) -> Result<(), ManagedProcessTreeError> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(ManagedProcessTreeError);
    }
    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    let mut resumed = false;
    let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    while has_entry {
        if entry.th32OwnerProcessID == process_id {
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if !thread.is_null() {
                let previous_count = unsafe { ResumeThread(thread) };
                unsafe {
                    CloseHandle(thread);
                }
                if previous_count != u32::MAX {
                    resumed = true;
                }
            }
        }
        has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    }
    unsafe {
        CloseHandle(snapshot);
    }
    if resumed {
        Ok(())
    } else {
        Err(ManagedProcessTreeError)
    }
}
