function r(t){return t<1024?`${t} B`:t<1024*1024?`${(t/1024).toFixed(1)} KB`:t<1024*1024*1024?`${(t/1024/1024).toFixed(1)} MB`:`${(t/1024/1024/1024).toFixed(2)} GB`}export{r as f};
