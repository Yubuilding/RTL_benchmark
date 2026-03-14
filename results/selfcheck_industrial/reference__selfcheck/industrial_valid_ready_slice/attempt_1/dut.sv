module valid_ready_slice(input logic clk, input logic rst_n, input logic in_valid, output logic in_ready, input logic [7:0] in_data, output logic out_valid, input logic out_ready, output logic [7:0] out_data);
  logic full;
  logic [7:0] data_q;

  assign in_ready = !full || (out_ready && out_valid);
  assign out_valid = full;
  assign out_data = data_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      full <= 1'b0;
      data_q <= 8'h00;
    end else if (in_valid && in_ready) begin
      full <= 1'b1;
      data_q <= in_data;
    end else if (out_ready && out_valid) begin
      full <= 1'b0;
    end
  end
endmodule
