`timescale 1ns/1ps
module tb;
  logic clk;
  logic rst_n;
  logic in_valid;
  logic in_ready;
  logic [7:0] in_data;
  logic out_valid;
  logic out_ready;
  logic [7:0] out_data;

  valid_ready_slice dut(
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(in_valid),
    .in_ready(in_ready),
    .in_data(in_data),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_data(out_data)
  );

  always #5 clk = ~clk;

  task step_check(input logic exp_in_ready, input logic exp_out_valid, input logic [7:0] exp_out_data);
    begin
      @(posedge clk);
      #1;
      if (in_ready !== exp_in_ready || out_valid !== exp_out_valid || out_data !== exp_out_data) begin
        $display("FAIL in_ready=%0b exp=%0b out_valid=%0b exp=%0b out_data=%0h exp=%0h", in_ready, exp_in_ready, out_valid, exp_out_valid, out_data, exp_out_data);
        $fatal(1);
      end
    end
  endtask

  initial begin
    clk = 0;
    rst_n = 0;
    in_valid = 0;
    in_data = 8'h00;
    out_ready = 0;

    step_check(1'b1, 1'b0, 8'h00);

    rst_n = 1;
    step_check(1'b1, 1'b0, 8'h00);

    in_valid = 1;
    in_data = 8'h3c;
    out_ready = 0;
    step_check(1'b0, 1'b1, 8'h3c);

    in_valid = 0;
    step_check(1'b0, 1'b1, 8'h3c);

    in_valid = 1;
    in_data = 8'ha5;
    out_ready = 1;
    step_check(1'b1, 1'b1, 8'ha5);

    in_valid = 0;
    out_ready = 1;
    step_check(1'b1, 1'b0, 8'ha5);

    in_valid = 1;
    in_data = 8'h5a;
    out_ready = 1;
    step_check(1'b1, 1'b1, 8'h5a);

    in_valid = 0;
    out_ready = 0;
    step_check(1'b0, 1'b1, 8'h5a);

    out_ready = 1;
    step_check(1'b1, 1'b0, 8'h5a);

    $display("PASS");
    $finish;
  end
endmodule
