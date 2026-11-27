fn main() {
    #[cfg(all(target_os = "windows", target_env = "msvc"))]
    {
        let mut res = winres::WindowsResource::new();
        res.set_icon("assets/icon.ico");
        res.set("ProductName", "KeyForge");
        res.set("FileDescription", "KeyForge Macro Studio");
        let _ = res.compile();
    }
}
