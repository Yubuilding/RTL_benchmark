`timescale 1ns/1ps
module tb;
  logic clk;
  logic rst_n;
  logic [3:0] req;
  logic [3:0] grant;

  rr_arb4 dut(.clk(clk), .rst_n(rst_n), .req(req), .grant(grant));

  always #5 clk = ~clk;

  task step_check(input logic [3:0] expected);
    begin
      @(posedge clk);
      #1;
      if (grant !== expected) begin
        $display("FAIL req=%0b expected grant=%0b got=%0b", req, expected, grant);
        $fatal(1);
      end
    end
  endtask

  initial begin
    clk = 0;
    rst_n = 0;
    req = 4'b0000;
    step_check(4'b0000);

    rst_n = 1;
    req = 4'b0101;
    step_check(4'b0001);
    step_check(4'b0100);
    step_check(4'b0001);

    req = 4'b1000;
    step_check(4'b1000);

    req = 4'b1010;
    step_check(4'b0010);
    step_check(4'b1000);

    req = 4'b0000;
    step_check(4'b0000);

    req = 4'b0110;
    step_check(4'b0010);
    step_check(4'b0100);

    $display("PASS");
    $finish;
  end
endmodule
