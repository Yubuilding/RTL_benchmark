module _rtl_benchmark_wave_dump;
  initial begin
    $dumpfile("simv.vcd");
    $dumpvars(0, tb);
  end
endmodule
